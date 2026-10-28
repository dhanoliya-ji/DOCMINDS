// Settings -> Webhooks (admins only): the outgoing webhooks that tell another system...

"use client";

import { ChevronDown, Loader2, Send, Webhook as WebhookIcon } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";

import HoldToConfirm from "@/components/app/HoldToConfirm";
import { Notice } from "@/components/AuthCard";
import { CopyButton } from "@/components/settings/MembersPanel";
import {
  ApiError,
  Webhook,
  WebhookCreated,
  WebhookDelivery,
  WebhookEventType,
  api,
  getErrorMessage,
} from "@/lib/api";
import { rich, useT } from "@/lib/i18n";

const ALL_EVENTS = "*";

export async function webhooksAvailable(): Promise<boolean> {
  try {
    await api.listWebhooks();
    return true;
  } catch {
    return false;
  }
}

function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "—";
}

export default function WebhooksPanel() {
  const { t } = useT();
  const [hooks, setHooks] = useState<Webhook[] | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [events, setEvents] = useState<WebhookEventType[]>([]);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [chosen, setChosen] = useState<string[]>([ALL_EVENTS]);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<WebhookCreated | null>(null);

  const [openId, setOpenId] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[] | null>(null);

  const load = useCallback(async () => {
    try {
      setHooks(await api.listWebhooks());
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setDisabled(true);
      else setError(getErrorMessage(err, t("webhooks.loadError")));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async: state is set after the fetch resolves
    load();
    api
      .listWebhookEvents()
      .then(setEvents)
      .catch(() => setEvents([]));
  }, [load]);

  if (disabled) return null;

  function toggleEvent(name: string) {
    setChosen((current) => {
      if (name === ALL_EVENTS) return current.includes(ALL_EVENTS) ? [] : [ALL_EVENTS];
      const rest = current.filter((e) => e !== ALL_EVENTS);
      return rest.includes(name) ? rest.filter((e) => e !== name) : [...rest, name];
    });
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    setStatus("");
    setCreating(true);
    try {
      setCreated(await api.createWebhook(url.trim(), chosen, description.trim() || undefined));
      setUrl("");
      setDescription("");
      setChosen([ALL_EVENTS]);
      await load();
    } catch (err) {
      setError(getErrorMessage(err, t("webhooks.createError")));
    } finally {
      setCreating(false);
    }
  }

  async function remove(hook: Webhook) {
    setError("");
    try {
      await api.deleteWebhook(hook.id);
      if (openId === hook.id) setOpenId(null);
      setStatus(t("webhooks.deleted", { url: hook.url }));
      await load();
    } catch (err) {
      setError(getErrorMessage(err, t("webhooks.deleteError")));
    }
  }

  async function sendTest(hook: Webhook) {
    setError("");
    try {
      await api.testWebhook(hook.id);
      setStatus(t("webhooks.testQueued", { url: hook.url }));
      if (openId === hook.id) setDeliveries(await api.listWebhookDeliveries(hook.id));
    } catch (err) {
      setError(getErrorMessage(err, t("webhooks.testError")));
    }
  }

  async function toggleDeliveries(hook: Webhook) {
    if (openId === hook.id) {
      setOpenId(null);
      return;
    }
    setOpenId(hook.id);
    setDeliveries(null);
    try {
      setDeliveries(await api.listWebhookDeliveries(hook.id));
    } catch (err) {
      setDeliveries([]);
      setError(getErrorMessage(err, t("webhooks.deliveriesError")));
    }
  }

  return (
    <div className="space-y-6">
      <form onSubmit={create} className="glass space-y-4 p-5" aria-label={t("webhooks.form")}>
        <div className="flex flex-wrap gap-3">
          <label className="min-w-[14rem] flex-[2] text-sm">
            <span className="mb-1 block" style={{ color: "var(--text-secondary)" }}>
              {t("webhooks.url")}
            </span>
            <input
              className="input"
              type="url"
              placeholder="https://example.com/hooks/docminds"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              maxLength={2048}
            />
          </label>
          <label className="min-w-[12rem] flex-1 text-sm">
            <span className="mb-1 block" style={{ color: "var(--text-secondary)" }}>
              {t("webhooks.description")}
            </span>
            <input
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={255}
            />
          </label>
        </div>
        <fieldset className="text-sm">
          <legend className="mb-2" style={{ color: "var(--text-secondary)" }}>
            {t("webhooks.events")}
          </legend>
          <div className="flex flex-wrap gap-x-4 gap-y-2">
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={chosen.includes(ALL_EVENTS)} onChange={() => toggleEvent(ALL_EVENTS)} />
              {t("webhooks.allEvents")}
            </label>
            {events
              .filter((e) => e.name !== ALL_EVENTS)
              .map((e) => (
                <label key={e.name} className="inline-flex items-center gap-2" title={e.description}>
                  <input
                    type="checkbox"
                    checked={chosen.includes(e.name)}
                    disabled={chosen.includes(ALL_EVENTS)}
                    onChange={() => toggleEvent(e.name)}
                  />
                  <code>{e.name}</code>
                </label>
              ))}
          </div>
        </fieldset>
        <button type="submit" className="btn-primary" disabled={creating || !url.trim() || chosen.length === 0}>
          {creating ? (
            <Loader2 size={15} className="animate-spin" aria-hidden="true" />
          ) : (
            <WebhookIcon size={15} aria-hidden="true" />
          )}
          {t("webhooks.add")}
        </button>
      </form>

      {created && (
        <Notice tone="success">
          {rich(t("webhooks.secret"), {
            url: created.url,
            secret: (
              <code className="break-all" data-testid="new-webhook-secret">
                {created.secret}
              </code>
            ),
            copy: <CopyButton text={created.secret} label={t("webhooks.copySecret")} />,
          })}
        </Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <p className="sr-only" aria-live="polite">
        {status}
      </p>
      {status && (
        <p className="text-sm" style={{ color: "var(--text-secondary)" }} aria-hidden="true">
          {status}
        </p>
      )}

      {hooks === null ? (
        <div className="glass shimmer h-24" aria-busy="true" />
      ) : hooks.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          {t("webhooks.empty")}
        </p>
      ) : (
        <ul className="space-y-3" aria-label={t("webhooks.list")}>
          {hooks.map((hook) => (
            <li key={hook.id} className="glass p-4 text-sm">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="break-all font-medium">{hook.url}</p>
                  {hook.description && <p style={{ color: "var(--text-secondary)" }}>{hook.description}</p>}
                  <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                    {hook.event_types.includes(ALL_EVENTS) ? t("webhooks.allEvents") : hook.event_types.join(", ")} ·{" "}
                    <span style={{ color: hook.is_active ? "var(--success)" : "var(--text-muted)" }}>
                      {hook.is_active ? t("webhooks.active") : t("webhooks.paused")}
                    </span>
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    onClick={() => sendTest(hook)}
                    disabled={!hook.is_active}
                    aria-label={t("webhooks.sendTestLabel", { url: hook.url })}
                  >
                    <Send size={13} aria-hidden="true" /> {t("webhooks.sendTest")}
                  </button>
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    onClick={() => toggleDeliveries(hook)}
                    aria-expanded={openId === hook.id}
                    aria-controls={`deliveries-${hook.id}`}
                  >
                    {t("webhooks.deliveries")}
                    <ChevronDown
                      size={13}
                      aria-hidden="true"
                      style={{ transform: openId === hook.id ? "rotate(180deg)" : undefined }}
                    />
                  </button>
                  <HoldToConfirm onConfirm={() => remove(hook)} aria-label={t("webhooks.deleteLabel", { url: hook.url })}>
                    {t("webhooks.holdDelete")}
                  </HoldToConfirm>
                </div>
              </div>

              {openId === hook.id && (
                <div id={`deliveries-${hook.id}`} className="mt-3 min-w-0 overflow-x-auto">
                  {deliveries === null ? (
                    <div className="shimmer h-10 rounded-lg" aria-busy="true" />
                  ) : deliveries.length === 0 ? (
                    <p style={{ color: "var(--text-secondary)" }}>{t("webhooks.noDeliveries")}</p>
                  ) : (
                    <table className="w-full text-left text-xs">
                      <caption className="sr-only">{t("webhooks.recent", { url: hook.url })}</caption>
                      <thead style={{ color: "var(--text-muted)" }}>
                        <tr>
                          <th className="py-1.5 font-medium">{t("webhooks.colEvent")}</th>
                          <th className="py-1.5 font-medium">{t("webhooks.colStatus")}</th>
                          <th className="py-1.5 font-medium">{t("webhooks.colAttempts")}</th>
                          <th className="py-1.5 font-medium">{t("webhooks.colResponse")}</th>
                          <th className="py-1.5 font-medium">{t("webhooks.colCreated")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {deliveries.map((d) => (
                          <tr key={d.id} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
                            <td className="py-1.5">
                              <code>{d.event_type}</code>
                            </td>
                            <td
                              className="py-1.5"
                              style={{
                                color:
                                  d.status === "delivered"
                                    ? "var(--success)"
                                    : d.status === "failed"
                                      ? "var(--danger)"
                                      : "var(--text-secondary)",
                              }}
                            >
                              {d.status}
                            </td>
                            <td className="py-1.5">{d.attempts}</td>
                            <td className="py-1.5 break-all" style={{ color: "var(--text-secondary)" }}>
                              {d.last_status_code ?? ""} {d.last_error ?? ""}
                            </td>
                            <td className="py-1.5" style={{ color: "var(--text-secondary)" }}>
                              {when(d.created_at)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
