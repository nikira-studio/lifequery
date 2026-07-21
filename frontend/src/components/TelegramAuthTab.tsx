import React, { useState, useEffect } from "react";
import { toast } from "@/hooks/use-toast";
import {
  AlertTriangle,
  Loader2,
  Key,
  Smartphone,
  ShieldCheck,
  Clock,
} from "lucide-react";
import {
  getTelegramStatus,
  startTelegramAuth,
  verifyTelegramAuth,
  disconnectTelegram,
  getSettings,
  type Settings,
  type TelegramStatus,
} from "@/api/settings";
import { SettingField } from "@/components/settings/SettingField";

interface TelegramAuthTabProps {
  settings: Settings;
  localSettings: Partial<Settings>;
  setLocalSettings: (s: Partial<Settings>) => void;
  handleSave: () => Promise<void>;
  saving: boolean;
  saveSuccess: boolean;
  telegramStatus: TelegramStatus;
  setTelegramStatus: (s: TelegramStatus) => void;
  onSettingsSaved?: (updated: Settings) => void;
}

export function TelegramAuthTab({
  settings,
  localSettings,
  setLocalSettings,
  handleSave,
  saving,
  saveSuccess,
  telegramStatus,
  setTelegramStatus,
}: TelegramAuthTabProps) {
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [authStatus, setAuthStatus] = useState<
    "idle" | "sending" | "verifying"
  >("idle");
  const [disconnecting, setDisconnecting] = useState(false);

  // Check Telegram status on mount (i.e., when the tab becomes active)
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const statusData = await getTelegramStatus();
        setTelegramStatus(statusData);
        if (statusData.state === "phone_sent") {
          if (statusData.phone && !phone) setPhone(statusData.phone);
          if (statusData.token && !authToken) setAuthToken(statusData.token);
        }
      } catch (err) {
        console.error("Failed to load Telegram status:", err);
      }
    };
    checkStatus();
  }, [phone, authToken]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleTelegramAuthStart = async () => {
    setAuthError(null);
    setCode("");
    setAuthToken(null);
    setAuthStatus("sending");

    try {
      const result = await startTelegramAuth(phone);
      setTelegramStatus(result);
      if (result.state === "phone_sent") {
        if ("token" in result && result.token) {
          setAuthToken(result.token);
        }
      }
    } catch (err) {
      const errorMsg =
        err instanceof Error ? err.message : "Failed to start authentication";
      setAuthError(errorMsg);
    } finally {
      setAuthStatus("idle");
    }
  };

  const handleTelegramAuthVerify = async () => {
    setAuthError(null);
    setAuthStatus("verifying");

    try {
      const result = await verifyTelegramAuth(
        authToken ? undefined : phone,
        code,
        authToken || undefined,
      );
      setTelegramStatus(result);
      setPhone("");
      setCode("");
      setAuthToken(null);

      if (result.state === "connected") {
        const freshStatus = await getTelegramStatus();
        setTelegramStatus(freshStatus);
        // Refresh settings to get the newly saved user identity
        const freshSettings = await getSettings();
        // Propagate updated settings to parent via setLocalSettings
        setLocalSettings(freshSettings);
      }
    } catch (err) {
      const errorMsg =
        err instanceof Error ? err.message : "Failed to verify code";
      setAuthError(errorMsg);
    } finally {
      setAuthStatus("idle");
    }
  };

  const handleTelegramDisconnect = async () => {
    setDisconnecting(true);
    try {
      await disconnectTelegram();
      setTelegramStatus({ state: "needs_auth" });
    } catch (err) {
      console.error("Failed to disconnect:", err);
      toast({
        title: "Failed to disconnect",
        description:
          err instanceof Error ? err.message : "An unexpected error occurred",
        variant: "destructive",
      });
    } finally {
      setDisconnecting(false);
    }
  };

  return (
    <section className="space-y-4">
      <h3 className="text-sm font-mono uppercase tracking-wider text-muted-foreground">
        Telegram Connection
      </h3>
      <div className="bg-card border border-border rounded-lg p-4 space-y-4">
        {/* API Credentials */}
        <div className="space-y-3">
          <h4 className="text-sm font-medium text-foreground">
            API Credentials
          </h4>
          <p className="text-xs text-muted-foreground">
            1. Go to{" "}
            <a
              href="https://my.telegram.org/apps"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              my.telegram.org/apps
            </a>{" "}
            and log in with your full phone number (include country code,
            e.g., 12135551212).
          </p>
          <p className="text-xs text-muted-foreground">
            2. Create a new application. You can use any name for App title
            and short name (e.g., "LifeQuery"). Leave URL empty. Select Web
            for Platform.
          </p>
          <p className="text-xs text-muted-foreground">
            3. Copy the <strong>App api_id</strong> and{" "}
            <strong>App api_hash</strong> (ignore the FCM credentials
            section - you don't need it).
          </p>
          <SettingField label="API ID">
            <input
              value={
                localSettings.telegram_api_id || settings.telegram_api_id
              }
              onChange={(e) =>
                setLocalSettings({
                  ...localSettings,
                  telegram_api_id: e.target.value,
                })
              }
              placeholder="Enter your Telegram API ID"
              className="input-field"
            />
          </SettingField>
          <SettingField label="API Hash">
            <input
              type="password"
              value={
                localSettings.telegram_api_hash ||
                settings.telegram_api_hash
              }
              onChange={(e) =>
                setLocalSettings({
                  ...localSettings,
                  telegram_api_hash: e.target.value,
                })
              }
              placeholder={
                settings.telegram_api_hash === "****"
                  ? "Leave empty to keep existing hash"
                  : "Enter your API hash"
              }
              className="input-field"
            />
          </SettingField>
        </div>

        {/* Sync Tuning */}
        <div className="space-y-3 pt-4 border-t border-border">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-primary" />
            <h4 className="text-sm font-medium text-foreground">
              Sync Tuning
            </h4>
          </div>
          <SettingField
            label={`Auto Sync Interval: ${localSettings.auto_sync_interval ?? settings.auto_sync_interval}m`}
          >
            <input
              type="range"
              min={5}
              max={1440}
              step={5}
              value={
                localSettings.auto_sync_interval ??
                settings.auto_sync_interval
              }
              onChange={(e) =>
                setLocalSettings({
                  ...localSettings,
                  auto_sync_interval: parseInt(e.target.value),
                })
              }
              className="w-full accent-primary"
            />
            <div className="flex justify-between text-[9px] text-muted-foreground font-mono mt-1">
              <span>5m</span>
              <span>24h</span>
            </div>
          </SettingField>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <SettingField
              label={`Fetch Batch: ${localSettings.telegram_fetch_batch ?? settings.telegram_fetch_batch}`}
            >
              <input
                type="range"
                min={100}
                max={3000}
                step={100}
                value={
                  localSettings.telegram_fetch_batch ??
                  settings.telegram_fetch_batch
                }
                onChange={(e) =>
                  setLocalSettings({
                    ...localSettings,
                    telegram_fetch_batch: parseInt(e.target.value),
                  })
                }
                className="w-full accent-primary"
              />
              <div className="flex justify-between text-[9px] text-muted-foreground font-mono mt-1">
                <span>100</span>
                <span>3000</span>
              </div>
            </SettingField>
            <SettingField
              label={`Fetch Wait: ${localSettings.telegram_fetch_wait ?? settings.telegram_fetch_wait}s`}
            >
              <input
                type="range"
                min={0}
                max={30}
                step={1}
                value={
                  localSettings.telegram_fetch_wait ??
                  settings.telegram_fetch_wait
                }
                onChange={(e) =>
                  setLocalSettings({
                    ...localSettings,
                    telegram_fetch_wait: parseInt(e.target.value),
                  })
                }
                className="w-full accent-primary"
              />
              <div className="flex justify-between text-[9px] text-muted-foreground font-mono mt-1">
                <span>0s</span>
                <span>30s</span>
              </div>
            </SettingField>
          </div>
          <p className="text-[10px] text-muted-foreground leading-tight mt-1">
            Tune these for performance. Lower wait/higher batch is faster
            but may trigger Telegram rate limits.
          </p>
        </div>

        {/* Save button for credentials */}
        <button
          onClick={handleSave}
          disabled={saving}
          className="w-full py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-40"
        >
          {saving ? (
            <>Saving...</>
          ) : saveSuccess ? (
            <>✓ Saved</>
          ) : (
            "Save Credentials"
          )}
        </button>

        {/* Connection Status & Form */}
        <div className="border-t border-border pt-4">
          {telegramStatus.state === "connected" ? (
            <div className="bg-success/5 border border-success/20 rounded-lg p-6 text-center space-y-4 animate-in zoom-in duration-300">
              <div className="w-16 h-16 bg-success/10 text-success rounded-full flex items-center justify-center mx-auto shadow-sm">
                <ShieldCheck className="w-8 h-8" />
              </div>
              <div>
                <h4 className="text-base font-semibold text-foreground">
                  Account Linked
                </h4>
                <p className="text-xs text-muted-foreground mt-1">
                  Your Telegram session is active and ready for syncing.
                </p>
                {(localSettings.user_first_name || settings.user_first_name) && (
                  <p className="text-xs text-primary font-medium mt-2">
                    Logged in as: {localSettings.user_first_name || settings.user_first_name}
                    {(localSettings.user_last_name || settings.user_last_name) &&
                      ` ${localSettings.user_last_name || settings.user_last_name}`}
                    {(localSettings.user_username || settings.user_username) &&
                      ` (@${localSettings.user_username || settings.user_username})`}
                  </p>
                )}
              </div>
              <div className="pt-2">
                <button
                  onClick={handleTelegramDisconnect}
                  disabled={disconnecting}
                  className="px-6 py-2 rounded-lg text-sm font-medium bg-destructive/10 text-destructive hover:bg-destructive/20 transition-all border border-destructive/20 disabled:opacity-40"
                >
                  {disconnecting ? "Disconnecting..." : "Disconnect Account"}
                </button>
              </div>
            </div>
          ) : telegramStatus.state === "uninitialized" ? (
            <div className="bg-muted/50 rounded-lg p-6 text-center space-y-3">
              <Key className="w-8 h-8 text-muted-foreground mx-auto opacity-50" />
              <p className="text-sm text-muted-foreground">
                Please configure and save your API credentials above to
                enable Telegram connection.
              </p>
            </div>
          ) : telegramStatus.state === "phone_sent" ? (
            <div className="space-y-4 animate-in slide-in-from-bottom-2 duration-300">
              <div className="flex items-center gap-2 text-sm text-primary font-medium">
                <Smartphone className="w-4 h-4 animate-pulse" />
                <span>Verification Required</span>
              </div>
              <p className="text-xs text-muted-foreground">
                We've sent a code via Telegram. Enter it below to link your
                account.
              </p>
              <div className="space-y-3">
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="Enter verification code"
                  className="input-field text-center text-lg tracking-[0.5em] font-mono"
                  maxLength={10}
                />
                <button
                  onClick={handleTelegramAuthVerify}
                  disabled={
                    !code || code.length < 3 || authStatus === "verifying"
                  }
                  className="w-full py-2.5 rounded-lg text-sm font-semibold bg-primary text-primary-foreground hover:opacity-90 shadow-sm transition-all disabled:opacity-40 flex items-center justify-center gap-2"
                >
                  {authStatus === "verifying" ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Verifying code...
                    </>
                  ) : (
                    "Complete Link"
                  )}
                </button>
                <button
                  onClick={() => setTelegramStatus({ state: "needs_auth" })}
                  className="w-full text-[11px] text-muted-foreground hover:text-foreground underline underline-offset-2"
                >
                  Cancel and try again
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-4 animate-in slide-in-from-bottom-2 duration-300">
              <div className="flex items-center gap-2 text-sm text-foreground font-medium">
                <Key className="w-4 h-4 text-primary" />
                <span>Link Telegram Account</span>
              </div>
              <p className="text-xs text-muted-foreground">
                Enter your phone number (including country code) to start
                the connection process.
              </p>
              <div className="space-y-3">
                <input
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="+1234567890"
                  className="input-field"
                />
                <button
                  onClick={handleTelegramAuthStart}
                  disabled={
                    !phone || phone.length < 10 || authStatus === "sending"
                  }
                  className="w-full py-2.5 rounded-lg text-sm font-semibold bg-primary text-primary-foreground hover:opacity-90 shadow-sm transition-all disabled:opacity-40 flex items-center justify-center gap-2"
                >
                  {authStatus === "sending" ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Authenticating...
                    </>
                  ) : (
                    "Authenticate with Telegram"
                  )}
                </button>
              </div>
            </div>
          )}

          {authError && (
            <div className="mt-4 bg-destructive/5 border border-destructive/20 rounded-md p-3 flex gap-3 items-start animate-in shake duration-300">
              <AlertTriangle className="w-4 h-4 text-destructive flex-shrink-0 mt-0.5" />
              <p className="text-xs text-destructive">{authError}</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
