/**
 * Hook for polling the backend health endpoint.
 */
import { useState, useEffect } from "react";

type HealthStatus = "checking" | "connected" | "disconnected";

export function useHealthCheck(intervalMs = 10000) {
  const [status, setStatus] = useState<HealthStatus>("checking");
  const [lastChecked, setLastChecked] = useState(Date.now());

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      try {
        const res = await fetch("/api/health");
        if (!cancelled) setStatus(res.ok ? "connected" : "disconnected");
      } catch {
        if (!cancelled) setStatus("disconnected");
      } finally {
        if (!cancelled) setLastChecked(Date.now());
      }
    };

    check();
    const interval = setInterval(check, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [intervalMs]);

  return { status, lastChecked };
}
