// Typed client for three optional backend features, kept out of lib/api.ts so that file...

import { Schema, apiFetch } from "@/lib/api";

export type Provider = Schema<"ProviderOut">;
export type Connection = Schema<"ConnectionOut">;
export type RemoteItem = Schema<"RemoteItemOut">;
export type RemoteItemPage = Schema<"RemoteItemPage">;
export type ImportJob = Schema<"ImportJobOut">;
export type ImportItem = Schema<"ImportItemOut">;
export type DisconnectResult = Schema<"DisconnectOut">;
export type LinkedDocument = Schema<"LinkedDocumentOut">;
export type SyncResult = Schema<"SyncResult">;
export type BillingStatus = Schema<"BillingStatus">;
export type Plan = Schema<"PlanOut">;
export type AnalyticsOverview = Schema<"AnalyticsOverview">;

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const platformApi = {
  listProviders() {
    return apiFetch<Provider[]>("/integrations/providers");
  },
  authorize(provider: string) {
    return apiFetch<{ authorization_url: string }>(
      `/integrations/${encodeURIComponent(provider)}/authorize`,
      { method: "POST" }
    );
  },
  listConnections() {
    return apiFetch<Connection[]>("/integrations/connections");
  },
  disconnect(connectionId: string) {
    return apiFetch<DisconnectResult>(`/integrations/connections/${connectionId}`, { method: "DELETE" });
  },
  browse(connectionId: string, options: { parent?: string; q?: string; cursor?: string } = {}) {
    return apiFetch<RemoteItemPage>(
      `/integrations/connections/${connectionId}/items${query(options)}`
    );
  },
  importItems(connectionId: string, projectId: string, itemIds: string[], autoSync = true) {
    return apiFetch<ImportJob>(`/integrations/connections/${connectionId}/import`, {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, item_ids: itemIds, auto_sync: autoSync }),
    });
  },
  getImport(importId: string) {
    return apiFetch<ImportJob>(`/integrations/imports/${importId}`);
  },
  listImports(connectionId?: string) {
    return apiFetch<ImportJob[]>(`/integrations/imports${query({ connection_id: connectionId })}`);
  },
  listLinkedDocuments(projectId?: string) {
    return apiFetch<LinkedDocument[]>(`/integrations/documents${query({ project_id: projectId })}`);
  },
  setAutoSync(documentId: string, autoSync: boolean) {
    return apiFetch<LinkedDocument>(`/integrations/documents/${documentId}`, {
      method: "PATCH",
      body: JSON.stringify({ auto_sync: autoSync }),
    });
  },
  syncDocument(documentId: string) {
    return apiFetch<SyncResult>(`/integrations/documents/${documentId}/sync`, { method: "POST" });
  },
  syncConnection(connectionId: string) {
    return apiFetch<SyncResult[]>(`/integrations/connections/${connectionId}/sync`, {
      method: "POST",
    });
  },

  getBilling() {
    return apiFetch<BillingStatus>("/billing");
  },
  checkout(plan: "pro" | "team") {
    return apiFetch<{ url: string }>("/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ plan }),
    });
  },
  openPortal() {
    return apiFetch<{ url: string }>("/billing/portal", { method: "POST" });
  },
  cancelSubscription() {
    return apiFetch<BillingStatus>("/billing/cancel", { method: "POST" });
  },

  getAnalytics(days = 30) {
    return apiFetch<AnalyticsOverview>(`/analytics/overview${query({ days })}`);
  },
};

async function probe(call: () => Promise<unknown>): Promise<boolean> {
  try {
    await call();
    return true;
  } catch {
    return false;
  }
}

export const integrationsAvailable = () => probe(() => platformApi.listProviders());
export const analyticsAvailable = () => probe(() => platformApi.getAnalytics(1));

export const navigate = {
  to(url: string) {
    window.location.assign(url);
  },
};
