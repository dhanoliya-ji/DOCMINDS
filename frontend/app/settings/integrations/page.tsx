/**
 * settings/integrations/page.tsx  (served at "/settings/integrations")
 * =====================================================================
 * Cloud imports, for editors and admins (/api/v1/integrations):
 *
 *   1. CONNECT a source -- Google Drive, OneDrive & SharePoint, Notion,
 *      Confluence. The button fetches the provider's consent URL and sends
 *      the browser there; the provider returns it to the API, which comes
 *      back here with ?connected=<provider> or ?error=<message>.
 *   2. BROWSE a connection: folders, spaces and pages open in place
 *      (with a trail back up), a name search, "Load more" for long folders.
 *      Importable items have a checkbox.
 *   3. IMPORT the selection into a project, optionally keeping it in sync.
 *      The import runs in the BACKGROUND on the server (the request answers
 *      202 with a job); this page polls the job every POLL_MS and shows each
 *      item's progress -- waiting, downloading, imported, failed (with the
 *      reason) -- until it is complete, then refreshes the documents table.
 *      Leaving and coming back resumes showing the newest unfinished job.
 *   4. The IMPORTED DOCUMENTS table: sync status, last sync, auto-sync on or
 *      off, and "Sync now".
 *
 * Disconnecting also revokes DocMinds' access at the provider where it has
 * an API (Google, Notion); for Microsoft and Confluence the page links to the
 * provider's own page for removing the app.
 *
 * While CONNECTORS_ENABLED is off the API answers 404 and this page says so.
 */

"use client";

import { ChevronRight, Cloud, FileText, Folder, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useCallback, useEffect, useState } from "react";

import HoldToConfirm from "@/components/app/HoldToConfirm";
import AppShell from "@/components/app/AppShell";
import LogoLoader from "@/components/app/LogoLoader";
import { Notice } from "@/components/AuthCard";
import { ApiError, Project, api, getErrorMessage, getToken } from "@/lib/api";
import { useT } from "@/lib/i18n";
import {
  Connection,
  ImportJob,
  LinkedDocument,
  Provider,
  RemoteItem,
  navigate,
  platformApi,
} from "@/lib/platform";

export default function IntegrationsPage() {
  return (
    <Suspense fallback={null}>
      <Integrations />
    </Suspense>
  );
}

type Crumb = { id: string | null; name: string };

/** How often a running import is polled. */
export const POLL_MS = 1500;

function Integrations() {
  const router = useRouter();
  const params = useSearchParams();
  const { t } = useT();

  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [documents, setDocuments] = useState<LinkedDocument[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // The picker.
  const [open, setOpen] = useState<Connection | null>(null);
  const [trail, setTrail] = useState<Crumb[]>([]);
  const [items, setItems] = useState<RemoteItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [projectId, setProjectId] = useState("");
  const [autoSync, setAutoSync] = useState(true);
  const [importing, setImporting] = useState(false);
  const [job, setJob] = useState<ImportJob | null>(null);
  // Names the picker showed, for items the server has not described yet.
  const [names, setNames] = useState<Record<string, string>>({});
  const [syncing, setSyncing] = useState<string | null>(null);
  const [manageUrl, setManageUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) router.push("/login");
  }, [router]);

  const refresh = useCallback(async () => {
    const [c, d] = await Promise.all([platformApi.listConnections(), platformApi.listLinkedDocuments()]);
    setConnections(c);
    setDocuments(d);
  }, []);

  // Poll a running import until it completes; then refresh the table.
  const jobDone = job?.status === "completed";
  useEffect(() => {
    if (!job || job.status === "completed") return;
    const jobId = job.id;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const next = await platformApi.getImport(jobId);
        if (cancelled) return;
        setJob(next);
        if (next.status === "completed") await refresh();
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err, "Could not check the import."));
      }
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [job, refresh]);

  useEffect(() => {
    let cancelled = false;
    platformApi
      .listProviders()
      .then(async (list) => {
        if (cancelled) return;
        setProviders(list);
        await refresh();
        // An import still running from an earlier visit: keep showing it.
        const recent = await platformApi.listImports().catch(() => [] as ImportJob[]);
        const running = recent.find((j) => j.status !== "completed");
        if (!cancelled && running) setJob(running);
        const p = await api.listProjects();
        if (!cancelled) {
          setProjects(p);
          setProjectId((current) => current || p[0]?.id || "");
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) setError(t("integrations.off"));
        else if (err instanceof ApiError && err.status === 403) setError(t("integrations.editorsOnly"));
        else setError(getErrorMessage(err, "Could not load cloud imports."));
      });
    return () => {
      cancelled = true;
    };
  }, [refresh, t]);

  const providerName = (id: string) => providers?.find((p) => p.id === id)?.name ?? id;
  const connected = params.get("connected");
  const callbackError = params.get("error");

  async function connect(provider: Provider) {
    setError("");
    try {
      const { authorization_url } = await platformApi.authorize(provider.id);
      navigate.to(authorization_url);
    } catch (err) {
      setError(getErrorMessage(err, "Could not start connecting."));
    }
  }

  async function load(connection: Connection, parent: string | null, q = "", next: string | null = null) {
    setBrowsing(true);
    setError("");
    try {
      const page = await platformApi.browse(connection.id, {
        parent: parent ?? undefined,
        q: q || undefined,
        cursor: next ?? undefined,
      });
      setNames((current) => ({ ...current, ...Object.fromEntries(page.items.map((i) => [i.id, i.name])) }));
      setItems((current) => (next ? [...current, ...page.items] : page.items));
      setCursor(page.next_cursor ?? null);
    } catch (err) {
      setError(getErrorMessage(err, "Could not list this source."));
    } finally {
      setBrowsing(false);
    }
  }

  function openConnection(connection: Connection) {
    setOpen(connection);
    setTrail([{ id: null, name: t("integrations.top") }]);
    setSelected([]);
    setSearch("");
    load(connection, null);
  }

  function enter(item: RemoteItem) {
    if (!open) return;
    setTrail((current) => [...current, { id: item.id, name: item.name }]);
    setSearch("");
    load(open, item.id);
  }

  function back(index: number) {
    if (!open) return;
    const crumb = trail[index];
    setTrail(trail.slice(0, index + 1));
    setSearch("");
    load(open, crumb.id);
  }

  function onSearch(event: FormEvent) {
    event.preventDefault();
    if (open) load(open, trail[trail.length - 1]?.id ?? null, search.trim());
  }

  function toggle(id: string) {
    setSelected((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current, id]));
  }

  async function runImport() {
    if (!open || !projectId || selected.length === 0) return;
    setImporting(true);
    setError("");
    try {
      setJob(await platformApi.importItems(open.id, projectId, selected, autoSync));
      setSelected([]);
    } catch (err) {
      setError(getErrorMessage(err, "The import failed."));
    } finally {
      setImporting(false);
    }
  }

  async function syncOne(doc: LinkedDocument) {
    setSyncing(doc.document_id);
    setNotice("");
    try {
      const result = await platformApi.syncDocument(doc.document_id);
      setNotice(
        result.last_error && result.outcome === "error"
          ? result.last_error
          : t(`integrations.outcome.${result.outcome}`)
      );
      await refresh();
    } catch (err) {
      setError(getErrorMessage(err, "Could not sync."));
    } finally {
      setSyncing(null);
    }
  }

  async function syncAll(connection: Connection) {
    setSyncing(connection.id);
    try {
      await platformApi.syncConnection(connection.id);
      await refresh();
    } catch (err) {
      setError(getErrorMessage(err, "Could not sync."));
    } finally {
      setSyncing(null);
    }
  }

  async function toggleAuto(doc: LinkedDocument) {
    try {
      await platformApi.setAutoSync(doc.document_id, !doc.auto_sync);
      await refresh();
    } catch (err) {
      setError(getErrorMessage(err, "Could not change auto-sync."));
    }
  }

  async function disconnect(connection: Connection) {
    setNotice("");
    setManageUrl(null);
    try {
      const result = await platformApi.disconnect(connection.id);
      if (open?.id === connection.id) setOpen(null);
      if (result?.revoked) {
        setNotice(t("integrations.revoked", { name: providerName(connection.provider) }));
      } else {
        setNotice(t("integrations.revokeManual", { name: providerName(connection.provider) }));
        setManageUrl(result?.manage_url ?? null);
      }
      await refresh();
    } catch (err) {
      setError(getErrorMessage(err, "Could not disconnect."));
    }
  }

  return (
    <AppShell>
      <Link href="/settings" className="mb-4 inline-block text-sm" style={{ color: "var(--text-secondary)" }}>
        ← {t("platform.back")}
      </Link>
      <h1 className="text-gradient mb-2 text-3xl font-bold">{t("integrations.title")}</h1>
      <p className="mb-6 max-w-2xl text-sm" style={{ color: "var(--text-secondary)" }}>
        {t("integrations.intro")}
      </p>

      {connected && <Notice tone="success">{t("integrations.connected", { name: providerName(connected) })}</Notice>}
      {callbackError && <Notice tone="error">{callbackError}</Notice>}
      {notice && (
        <Notice tone="info">
          {notice}
          {manageUrl && (
            <>
              {" "}
              <a href={manageUrl} target="_blank" rel="noopener noreferrer" className="underline">
                {t("integrations.manageAccess")}
              </a>
            </>
          )}
        </Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}

      {!providers ? (
        !error && <LogoLoader label={t("platform.loading")} />
      ) : (
        <div className="mt-6 space-y-8">
          {/* ---- 1. connect ---- */}
          <section aria-labelledby="providers-heading">
            <h2 id="providers-heading" className="mb-3 text-lg font-semibold">
              {t("integrations.providers")}
            </h2>
            <div className="flex flex-wrap gap-2">
              {providers.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className="btn-ghost"
                  disabled={!p.configured}
                  onClick={() => connect(p)}
                >
                  <Cloud className="h-4 w-4" aria-hidden="true" />
                  {p.configured ? t("integrations.connect", { name: p.name }) : t("integrations.notConfigured", { name: p.name })}
                </button>
              ))}
            </div>
          </section>

          {/* ---- connections ---- */}
          <section aria-labelledby="connections-heading">
            <h2 id="connections-heading" className="mb-3 text-lg font-semibold">
              {t("integrations.connections")}
            </h2>
            {connections.length === 0 ? (
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                {t("integrations.noConnections")}
              </p>
            ) : (
              <ul className="space-y-2">
                {connections.map((c) => (
                  <li key={c.id} className="glass flex flex-wrap items-center justify-between gap-3 p-4">
                    <div className="min-w-0">
                      <div className="font-medium">{providerName(c.provider)}</div>
                      <div className="break-words text-sm" style={{ color: "var(--text-secondary)" }}>
                        {c.account_label}
                        {c.status !== "active" && (
                          <span style={{ color: "var(--warning)" }}> · {t("integrations.reconnect")}</span>
                        )}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" className="btn-primary" onClick={() => openConnection(c)}>
                        {t("integrations.browse")}
                      </button>
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={syncing === c.id}
                        onClick={() => syncAll(c)}
                      >
                        <RefreshCw className="h-4 w-4" aria-hidden="true" />
                        {t("integrations.syncAll")}
                      </button>
                      <HoldToConfirm
                        onConfirm={() => disconnect(c)}
                        aria-label={`${t("integrations.disconnect")}: ${providerName(c.provider)} ${c.account_label}`}
                      >
                        {t("integrations.disconnect")}
                      </HoldToConfirm>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* ---- 2 + 3. picker and import ---- */}
          {open && (
            <section aria-labelledby="picker-heading" className="glass space-y-4 p-5">
              <h2 id="picker-heading" className="text-lg font-semibold">
                {t("integrations.picker")} · {providerName(open.provider)}
              </h2>
              <nav aria-label={t("integrations.picker")} className="flex flex-wrap items-center gap-1 text-sm">
                {trail.map((crumb, i) => (
                  <span key={`${crumb.id}-${i}`} className="flex items-center gap-1">
                    {i > 0 && <ChevronRight className="h-3 w-3" aria-hidden="true" />}
                    {i === trail.length - 1 ? (
                      <span aria-current="page">{crumb.name}</span>
                    ) : (
                      <button type="button" className="underline" onClick={() => back(i)}>
                        {crumb.name}
                      </button>
                    )}
                  </span>
                ))}
              </nav>
              <form onSubmit={onSearch} className="flex gap-2" role="search">
                <input
                  className="input"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t("integrations.search")}
                  aria-label={t("integrations.search")}
                />
                <button type="submit" className="btn-ghost">
                  {t("integrations.searchButton")}
                </button>
              </form>

              {browsing && items.length === 0 ? (
                <Loader2 className="h-5 w-5 animate-spin" aria-label={t("platform.loading")} />
              ) : items.length === 0 ? (
                <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                  {t("integrations.empty")}
                </p>
              ) : (
                <ul className="max-h-96 divide-y overflow-auto" style={{ borderColor: "var(--border-subtle)" }}>
                  {items.map((item) => (
                    <li key={item.id} className="flex items-center justify-between gap-3 py-2">
                      <label className="flex min-w-0 items-center gap-2">
                        {item.importable ? (
                          <input
                            type="checkbox"
                            checked={selected.includes(item.id)}
                            onChange={() => toggle(item.id)}
                          />
                        ) : (
                          <span className="inline-block w-[13px]" aria-hidden="true" />
                        )}
                        {item.kind === "folder" ? (
                          <Folder className="h-4 w-4 shrink-0" aria-hidden="true" />
                        ) : (
                          <FileText className="h-4 w-4 shrink-0" aria-hidden="true" />
                        )}
                        <span className="truncate">{item.name}</span>
                      </label>
                      {(item.kind === "folder" || item.has_children) && (
                        <button
                          type="button"
                          className="btn-ghost text-xs"
                          onClick={() => enter(item)}
                          aria-label={`${t("integrations.open")} ${item.name}`}
                        >
                          {t("integrations.open")}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {cursor && (
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={browsing}
                  onClick={() => load(open, trail[trail.length - 1]?.id ?? null, search.trim(), cursor)}
                >
                  {t("integrations.more")}
                </button>
              )}

              <div className="flex flex-wrap items-end gap-4 border-t pt-4" style={{ borderColor: "var(--border-subtle)" }}>
                <label className="flex flex-col gap-1 text-sm">
                  <span>{t("integrations.project")}</span>
                  <select className="input" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={autoSync} onChange={(e) => setAutoSync(e.target.checked)} />
                  {t("integrations.autoSync")}
                </label>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={importing || selected.length === 0 || !projectId || (job !== null && !jobDone)}
                  onClick={runImport}
                >
                  {importing ? t("integrations.importing") : t("integrations.import", { count: selected.length })}
                </button>
              </div>

            </section>
          )}

          {/* ---- the background import's progress ---- */}
          {job && (
            <section aria-labelledby="import-heading" className="glass space-y-2 p-5">
              <h2 id="import-heading" className="flex items-center gap-2 text-lg font-semibold">
                {!jobDone && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {jobDone ? t("integrations.importDone") : t("integrations.importRunning")}
              </h2>
              <ul className="space-y-1 text-sm" aria-live="polite">
                {job.items.map((r) => (
                  <li key={r.item_id} className="break-words">
                    <span className="font-medium">{r.name || names[r.item_id] || r.item_id}</span>
                    {": "}
                    <strong
                      style={{
                        color:
                          r.status === "failed"
                            ? "var(--danger)"
                            : r.status === "imported"
                              ? "var(--success)"
                              : "var(--text-secondary)",
                      }}
                    >
                      {t(`integrations.result.${r.status}`)}
                    </strong>
                    {r.status !== "imported" && r.detail ? ` — ${r.detail}` : ""}
                  </li>
                ))}
              </ul>
              {jobDone && (
                <button type="button" className="btn-ghost text-xs" onClick={() => setJob(null)}>
                  {t("integrations.dismiss")}
                </button>
              )}
            </section>
          )}

          {/* ---- 4. imported documents ---- */}
          <section aria-labelledby="documents-heading">
            <h2 id="documents-heading" className="mb-3 text-lg font-semibold">
              {t("integrations.documents")}
            </h2>
            {documents.length === 0 ? (
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                {t("integrations.noDocuments")}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <caption className="sr-only">{t("integrations.documents")}</caption>
                  <thead style={{ color: "var(--text-muted)" }}>
                    <tr>
                      <th scope="col" className="py-2 font-medium">{t("integrations.col.document")}</th>
                      <th scope="col" className="py-2 font-medium">{t("integrations.col.source")}</th>
                      <th scope="col" className="py-2 font-medium">{t("integrations.col.status")}</th>
                      <th scope="col" className="py-2 font-medium">{t("integrations.col.lastSynced")}</th>
                      <th scope="col" className="py-2 font-medium">{t("integrations.col.auto")}</th>
                      <th scope="col" className="py-2 font-medium">{t("integrations.col.actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {documents.map((d) => (
                      <tr key={d.document_id} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
                        <td className="break-words py-2 pr-3">
                          {d.web_url ? (
                            <a href={d.web_url} target="_blank" rel="noopener noreferrer" className="underline">
                              {d.filename}
                            </a>
                          ) : (
                            d.filename
                          )}
                        </td>
                        <td className="py-2 pr-3">{providerName(d.provider)}</td>
                        <td className="py-2 pr-3" title={d.last_error ?? undefined}>
                          <span style={{ color: d.sync_status === "synced" ? "var(--success)" : d.sync_status === "syncing" ? "var(--text-secondary)" : "var(--warning)" }}>
                            ●
                          </span>{" "}
                          {t(`integrations.status.${d.sync_status}`)}
                        </td>
                        <td className="py-2 pr-3">
                          {d.last_synced_at ? new Date(d.last_synced_at).toLocaleString() : "—"}
                        </td>
                        <td className="py-2 pr-3">
                          <input
                            type="checkbox"
                            checked={d.auto_sync}
                            onChange={() => toggleAuto(d)}
                            aria-label={`${t("integrations.col.auto")}: ${d.filename}`}
                          />
                        </td>
                        <td className="py-2">
                          <button
                            type="button"
                            className="btn-ghost text-xs"
                            disabled={syncing === d.document_id || !d.connection_id}
                            onClick={() => syncOne(d)}
                            aria-label={`${t("integrations.syncNow")}: ${d.filename}`}
                          >
                            {t("integrations.syncNow")}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </AppShell>
  );
}
