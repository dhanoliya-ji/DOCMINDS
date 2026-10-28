// Two small things that make the web app installable.

"use client";

import { Download, X } from "lucide-react";
import { useEffect, useState } from "react";

import { useT } from "@/lib/i18n";

const DISMISSED_KEY = "docminds-install-dismissed";

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

function dismissed(): boolean {
  try {
    return localStorage.getItem(DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

export default function PwaSupport() {
  const { t } = useT();
  const [deferred, setDeferred] = useState<InstallPromptEvent | null>(null);

  useEffect(() => {
    if (process.env.NODE_ENV === "production" && "serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        /* no worker: the app works exactly as before, just not offline */
      });
    }
    function onPrompt(event: Event) {
      event.preventDefault();
      if (!dismissed()) setDeferred(event as InstallPromptEvent);
    }
    const onInstalled = () => setDeferred(null);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (!deferred) return null;

  function close() {
    try {
      localStorage.setItem(DISMISSED_KEY, "1");
    } catch {
      /* offered again next visit */
    }
    setDeferred(null);
  }

  async function install() {
    const event = deferred;
    if (!event) return;
    await event.prompt();
    await event.userChoice.catch(() => null);
    setDeferred(null);
  }

  return (
    <section
      aria-label={t("pwa.installTitle")}
      className="glass fixed bottom-4 left-4 right-4 z-40 flex items-start gap-3 p-4 sm:left-auto sm:max-w-sm"
      style={{ background: "var(--bg-elevated)" }}
    >
      <Download size={18} aria-hidden="true" className="mt-0.5 shrink-0" style={{ color: "var(--accent-hover)" }} />
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium">{t("pwa.installTitle")}</p>
        <p className="mt-1" style={{ color: "var(--text-secondary)" }}>
          {t("pwa.installBody")}
        </p>
        <div className="mt-3 flex gap-2">
          <button type="button" className="btn-primary text-xs" onClick={install}>
            {t("pwa.install")}
          </button>
          <button type="button" className="btn-ghost text-xs" onClick={close}>
            {t("pwa.dismiss")}
          </button>
        </div>
      </div>
      <button type="button" onClick={close} aria-label={t("pwa.dismiss")} style={{ color: "var(--text-muted)" }}>
        <X size={16} aria-hidden="true" />
      </button>
    </section>
  );
}
