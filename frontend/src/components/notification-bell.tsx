import { useEffect, useState } from "react";
import { Bell, BellOff } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type BellState = "hidden" | "loading" | "off" | "on";

interface PushConfig {
  enabled: boolean;
  public_key: string;
}

function applicationServerKey(value: string): ArrayBuffer {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index);
  }
  return bytes.buffer;
}

async function persistSubscription(subscription: PushSubscription): Promise<void> {
  const data = subscription.toJSON();
  if (!data.endpoint || !data.keys?.p256dh || !data.keys.auth) {
    throw new Error("Ongeldig pushabonnement");
  }
  await api.pushSubscribe({
    endpoint: data.endpoint,
    keys: { p256dh: data.keys.p256dh, auth: data.keys.auth },
  });
}

export function NotificationBell() {
  const { user } = useAuth();
  const [state, setState] = useState<BellState>("loading");
  const [config, setConfig] = useState<PushConfig | null>(null);

  const eligible = Boolean(
    user &&
      !user.is_platform_admin &&
      (user.role === "owner" || user.role === "member" || user.role === "courier"),
  );

  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      if (
        !eligible ||
        !("serviceWorker" in navigator) ||
        !("PushManager" in window) ||
        !("Notification" in window)
      ) {
        if (!cancelled) setState("hidden");
        return;
      }
      try {
        const nextConfig = (await api.pushConfig()) as PushConfig;
        if (!nextConfig.enabled || !nextConfig.public_key) {
          if (!cancelled) setState("hidden");
          return;
        }
        const registration = await navigator.serviceWorker.register("/sw.js");
        const subscription = await registration.pushManager.getSubscription();
        if (subscription && Notification.permission === "granted") {
          // Rebind an existing browser endpoint to the currently logged-in user.
          await persistSubscription(subscription);
        }
        if (!cancelled) {
          setConfig(nextConfig);
          setState(
            subscription && Notification.permission === "granted" ? "on" : "off",
          );
        }
      } catch {
        if (!cancelled) setState("hidden");
      }
    }

    void initialize();
    return () => {
      cancelled = true;
    };
  }, [eligible, user?.id]);

  async function toggle() {
    if (!config || state === "loading") return;
    setState("loading");
    try {
      const registration = await navigator.serviceWorker.ready;
      const existing = await registration.pushManager.getSubscription();
      if (existing) {
        await api.pushUnsubscribe(existing.endpoint);
        await existing.unsubscribe();
        setState("off");
        return;
      }

      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setState("off");
        return;
      }
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: applicationServerKey(config.public_key),
      });
      try {
        await persistSubscription(subscription);
        setState("on");
      } catch (error) {
        await subscription.unsubscribe();
        throw error;
      }
    } catch {
      setState("off");
    }
  }

  if (state === "hidden") return null;
  const enabled = state === "on";
  const label = enabled ? "Pushmeldingen uitschakelen" : "Pushmeldingen inschakelen";

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={state === "loading"}
      aria-label={label}
      aria-pressed={enabled}
      title={label}
      className="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
    >
      {enabled ? <Bell className="h-4 w-4" /> : <BellOff className="h-4 w-4" />}
    </button>
  );
}
