/**
 * Settings API — wraps /api/settings and /api/telegram/* endpoints.
 */
import { apiFetch } from "./client";

export interface Settings {
  telegram_api_id: string;
  telegram_api_hash: string;
  telegram_fetch_batch: number;
  telegram_fetch_wait: number;
  ollama_url: string;
  embedding_model: string;
  chat_provider: string;
  chat_model: string;
  chat_url: string;
  chat_api_key: string;
  openrouter_api_key: string;
  custom_chat_url: string;
  temperature: number;
  max_tokens: number;
  top_k: number;
  context_cap: number;
  chunk_target: number;
  chunk_max: number;
  chunk_overlap: number;
  api_key: string;
  auto_sync_interval: number;
  enable_thinking: boolean;
  enable_rag: boolean;
  system_prompt: string;
  user_first_name: string;
  user_last_name: string;
  user_username: string;
  noise_filter_keywords?: string;
}

export type SettingsUpdate = Partial<Settings>;

export interface TelegramStatus {
  state: "uninitialized" | "needs_auth" | "phone_sent" | "connected" | "error";
  detail?: string | null;
  phone?: string;
  token?: string;
}

export interface ProviderProfile {
  id: string;
  name: string;
  provider_type: string;
  base_url: string | null;
  api_key: string | null;
  last_model: string | null;
}

export async function getSettings(): Promise<Settings> {
  return apiFetch<Settings>("/settings");
}

export async function saveSettings(update: SettingsUpdate): Promise<void> {
  await apiFetch<{ ok: boolean }>("/settings", {
    method: "POST",
    body: JSON.stringify(update),
  });
}

export async function getTelegramStatus(): Promise<TelegramStatus> {
  return apiFetch<TelegramStatus>("/telegram/status");
}

export async function getProviders(): Promise<ProviderProfile[]> {
  return apiFetch<ProviderProfile[]>("/providers");
}

export interface AuthStartResponse {
  state: string;
  token?: string;
}

export async function startTelegramAuth(
  phone: string,
): Promise<AuthStartResponse> {
  return apiFetch<AuthStartResponse>("/telegram/auth/start", {
    method: "POST",
    body: JSON.stringify({ phone }),
  });
}

export interface VerifyResponse {
  state: string;
  token?: string;
  error?: string;
}

export async function verifyTelegramAuth(
  phone: string | undefined,
  code: string,
  token?: string,
  password?: string,
): Promise<VerifyResponse> {
  return apiFetch<VerifyResponse>("/telegram/auth/verify", {
    method: "POST",
    body: JSON.stringify({ code, phone, token, password }),
  });
}

export async function disconnectTelegram(): Promise<void> {
  await apiFetch<{ state: string }>("/telegram/disconnect", {
    method: "POST",
  });
}
