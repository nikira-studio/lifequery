/**
 * Hook for fetching available models from the backend.
 * The backend returns { models, embedding_models, chat_models } as string arrays.
 * Supports refetching with optional provider/url/api_key overrides for live preview.
 */
import { useState, useEffect, useCallback } from "react";
import { apiFetch } from "@/api/client";

interface ModelsResponse {
  models: string[];
  embedding_models: string[];
  chat_models: string[];
}

interface RefetchOptions {
  provider?: string;
  url?: string;
  api_key?: string;
}

interface UseModelsOptions {
  filter?: "embedding" | "chat" | "all";
  provider?: string;
  url?: string;
  api_key?: string;
}

interface UseModelsResult {
  models: string[];
  loading: boolean;
  error: string | null;
  refetch: (opts?: RefetchOptions) => void;
}

export function useModels({ filter = "all", provider, url, api_key }: UseModelsOptions = {}): UseModelsResult {
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refetchTrigger, setRefetchTrigger] = useState<RefetchOptions | null>(null);

  const refetch = useCallback((opts?: RefetchOptions) => {
    setRefetchTrigger(opts ?? {});
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        setLoading(true);
        setError(null);

        const params = new URLSearchParams();
        const activeProvider = refetchTrigger?.provider ?? provider;
        const activeUrl = refetchTrigger?.url ?? url;
        const activeKey = refetchTrigger?.api_key ?? api_key;

        if (activeProvider) params.set("provider", activeProvider);
        if (activeUrl) params.set("url", activeUrl);
        if (activeKey) params.set("api_key", activeKey);

        const query = params.toString();
        const path = query ? `/models?${query}` : "/models";

        const data = await apiFetch<ModelsResponse>(path);
        if (!cancelled) {
          if (filter === "embedding") {
            setModels(data.embedding_models);
          } else if (filter === "chat") {
            setModels(data.chat_models);
          } else {
            setModels(data.models);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load models");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [filter, refetchTrigger, provider, url, api_key]);

  return { models, loading, error, refetch };
}
