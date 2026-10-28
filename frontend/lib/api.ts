// Every single conversation between the browser and the Python backend goes through this...

import type { components } from "@/lib/api-types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const API_V1 = `${API_BASE}/api/v1`;

const TOKEN_KEY = "edia_token";
const REFRESH_KEY = "edia_refresh";


export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_KEY);
}

export function setRefreshToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(REFRESH_KEY, token);
}


let refreshInFlight: Promise<boolean> | null = null;

export function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      const refreshToken = getRefreshToken();
      if (!refreshToken) return false;
      try {
        const response = await fetch(`${API_V1}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!response.ok) return false;
        const data = await response.json();
        setToken(data.access_token);
        setRefreshToken(data.refresh_token);
        return true;
      } catch {
        return false;
      }
    })().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

function endSession(): never {
  clearToken();
  if (typeof window !== "undefined") {
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login";
  }
  throw new Error("Your session expired. Please sign in again.");
}


export class ApiError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;

  constructor(message: string, status: number, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

function readRetryAfter(response: Response): number | null {
  const raw = response.headers?.get?.("Retry-After");
  if (!raw) return null;
  const seconds = Number(raw);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds);
  const date = Date.parse(raw);
  if (Number.isNaN(date)) return null;
  return Math.max(0, Math.ceil((date - Date.now()) / 1000));
}

async function errorFromResponse(response: Response, fallback: string): Promise<ApiError> {
  let message = fallback;
  try {
    const body = await response.json();
    if (body && body.detail) {
      message =
        typeof body.detail === "string" ? body.detail : body.detail[0]?.msg || message;
    }
  } catch {
    // The error body was not JSON at all -- keep the fallback.
  }
  return new ApiError(message, response.status, readRetryAfter(response));
}

export function formatWait(seconds: number): string {
  if (seconds < 90) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  if (seconds < 90 * 60) return `${Math.ceil(seconds / 60)} minutes`;
  if (seconds < 48 * 3600) return `${Math.ceil(seconds / 3600)} hours`;
  return `${Math.ceil(seconds / 86400)} days`;
}


export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  isRetry = false
): Promise<T> {
  const token = getToken();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_V1}${path}`, { ...options, headers });

  if (response.status === 401) {
    if (!isRetry && (await refreshSession())) {
      return apiFetch<T>(path, options, true);
    }
    endSession();
  }

  if (!response.ok) {
    throw await errorFromResponse(response, `Request failed (${response.status})`);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

export function getErrorMessage(error: unknown, fallback = "Something went wrong."): string {
  if (error instanceof ApiError && error.message) {
    if (
      error.status === 429 &&
      error.retryAfter !== null &&
      !/try again/i.test(error.message)
    ) {
      return `${error.message} Try again in ${formatWait(error.retryAfter)}.`;
    }
    return error.message;
  }
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error) return error;
  return fallback;
}

export interface DocumentMeta {
  chunk_count?: number;
  chunk_strategy?: string;
  chunk_size?: number;
  chunk_overlap?: number;
  word_count?: number;
  char_count?: number;
  token_count?: number;
  page_count?: number;
  language?: string;
  needs_ocr?: boolean;
  ocr_applied?: boolean;
  embedding_model?: string;
  embedding_provider?: string;
  embedding_dimension?: number;
  processing_seconds?: number;
  header_section?: string;
  error?: string;
  [key: string]: string | number | boolean | undefined;
}


export type Schema<Name extends keyof components["schemas"]> = components["schemas"][Name];

export type Project = Schema<"ProjectOut">;

export interface DocumentItem {
  id: string;
  filename: string;
  file_type: string;
  file_size: number;
  status: string;
  version: number;
  meta_data: DocumentMeta;
  error_message?: string | null;
  attempts?: number;
  project_id: string;
  created_at: string;
  updated_at: string;
}

export interface SearchResultItem {
  chunk_id: string;
  document_id: string;
  filename: string;
  page_number: number | null;
  chunk_index: number;
  content: string;
  score: number;
  meta_data: DocumentMeta;
  match_type?: "vector" | "keyword" | "both" | null;
}

export interface SearchResponse {
  query: string;
  results: SearchResultItem[];
  count: number;
  took_seconds: number;
}

export interface CitationItem {
  number: number;
  chunk_id: string;
  document_id: string;
  filename: string;
  page_number: number | null;
  score: number;
  snippet: string;
}

export interface ChatMessageItem {
  id: string;
  session_id: string;
  role: string;
  content: string;
  tokens_used: number | null;
  latency: number | null;
  citations: CitationItem[];
  mode?: ChatMode | null;
  model?: string | null;
  steps?: AgentStep[];
  confidence?: Confidence | null;
  created_at: string;
}

export interface ConfidenceReason {
  code: string;
  value?: number;
}

export interface Confidence {
  level: "high" | "medium" | "low";
  score: number;
  retrieval: number;
  coverage: number;
  reasons: ConfidenceReason[];
}

export type ChatSessionItem = Schema<"ChatSessionOut">;

export type ChatMode = "rag" | "agent";


export type Role = "Admin" | "Manager" | "Employee";
export type RoleInput = Role | "admin" | "editor" | "viewer";

export type Member = Schema<"MemberOut">;

export type Invite = Schema<"InviteOut">;

export type InviteCreated = Schema<"InviteCreated">;

export type InvitePreview = Schema<"InvitePreview">;

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  role: Role;
  user_id: string;
  is_active: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export type AuditLogItem = Schema<"AuditLogOut">;

export type AuditLogPage = Schema<"AuditLogPage">;

export interface AuditLogFilters {
  limit?: number;
  offset?: number;
  action?: string;
  user_id?: string;
  since?: string;
  until?: string;
}


export interface CurrentUser {
  id: string;
  email: string;
  role: Role;
  org_id: string;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
  updated_at: string;
}

export type QuotaUsage = Schema<"QuotaUsage">;

export type UsageSummary = Schema<"UsageSummary">;

export type AgentStep = Schema<"AgentStep">;

export interface ChatResponse {
  session_id: string;
  user_message: ChatMessageItem;
  assistant_message: ChatMessageItem;
  citations: CitationItem[];
  model: string;
  mode?: ChatMode;
  steps?: AgentStep[];
  search_query?: string | null;
  confidence?: Confidence | null;
}

export type ShareKind = "message" | "session" | "project";
export type ShareLink = Schema<"ShareLinkOut">;
export type ShareLinkCreated = Schema<"ShareLinkCreated">;
export type PublicShare = Schema<"PublicShare">;
export type PromptTemplate = Schema<"PromptTemplateOut">;
export type PromptScope = "personal" | "org";
export type Comparison = Schema<"ComparisonOut">;
export type ComparisonCitation = Schema<"ComparisonCitation">;
export type Difference = Schema<"Difference">;


export type ProjectDetail = Schema<"ProjectDetail">;

export type ChunkContext = Schema<"ChunkContextOut">;

export interface ReindexOptions {
  chunk_strategy?: "fixed_size" | "sentence" | "markdown";
  chunk_size?: number;
  chunk_overlap?: number;
}

export type FeedbackBreakdown = Schema<"FeedbackBreakdown">;

export type EmbeddingMap = Schema<"EmbeddingMap">;
export type EmbeddingPoint = Schema<"EmbeddingPoint">;

export type Webhook = Schema<"WebhookOut">;
export type WebhookCreated = Schema<"WebhookCreated">;
export type WebhookDelivery = Schema<"WebhookDeliveryOut">;
export type WebhookEventType = Schema<"WebhookEventType">;

export interface FeedbackSummary {
  total: number;
  up: number;
  down: number;
  satisfaction: number | null;
  by_mode: FeedbackBreakdown[];
  by_model: FeedbackBreakdown[];
  recent_negative: {
    message_id: string;
    session_id: string;
    rating: number;
    comment: string | null;
    question: string | null;
    answer: string;
    mode: string | null;
    model: string | null;
    created_at: string;
  }[];
}

export interface AskOptions {
  mode?: ChatMode;
  documentIds?: string[];
  signal?: AbortSignal;
}

export interface StreamHandlers {
  onStart?: (event: { session_id: string; user_message_id: string; mode: ChatMode; title: string }) => void;
  onQuery?: (event: { search_query: string; rewritten: boolean }) => void;
  onStep?: (step: AgentStep) => void;
  onCitations?: (citations: CitationItem[]) => void;
  onToken?: (text: string) => void;
  onReset?: () => void;
}

export interface SseEvent {
  event: string;
  data: string;
}

export function parseSseBuffer(buffer: string): { events: SseEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const frames = normalized.split("\n\n");
  const rest = frames.pop() ?? "";
  const events: SseEvent[] = [];
  for (const frame of frames) {
    let event = "message";
    const data: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length > 0) events.push({ event, data: data.join("\n") });
  }
  return { events, rest };
}


export const api = {

  async signup(email: string, password: string, orgName: string, inviteToken?: string) {
    return apiFetch<{ id: string; email: string; role: string }>(
      "/auth/signup",
      {
        method: "POST",
        body: JSON.stringify({
          email,
          password,
          ...(inviteToken ? { invite_token: inviteToken } : { org_name: orgName }),
        }),
      }
    );
  },

  previewInvite(token: string) {
    return apiFetch<InvitePreview>(`/auth/invites/${encodeURIComponent(token)}`);
  },

  requestPasswordReset(email: string) {
    return apiFetch<{ detail: string }>("/auth/password-reset/request", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
  },

  confirmPasswordReset(token: string, newPassword: string) {
    return apiFetch<{ detail: string }>("/auth/password-reset/confirm", {
      method: "POST",
      body: JSON.stringify({ token, new_password: newPassword }),
    });
  },

  verifyEmail(token: string) {
    return apiFetch<{ detail: string }>("/auth/verify-email", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
  },

  resendVerification() {
    return apiFetch<{ detail: string }>("/auth/verify-email/resend", { method: "POST" });
  },

  getMe() {
    return apiFetch<CurrentUser>("/users/me");
  },


  listMembers() {
    return apiFetch<Member[]>("/org/members");
  },

  updateMember(userId: string, changes: { role?: RoleInput; is_active?: boolean }) {
    return apiFetch<Member>(`/org/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    });
  },

  removeMember(userId: string) {
    return apiFetch<void>(`/org/members/${userId}`, { method: "DELETE" });
  },

  createInvite(email: string, role: RoleInput = "viewer") {
    return apiFetch<InviteCreated>("/org/invites", {
      method: "POST",
      body: JSON.stringify({ email, role }),
    });
  },

  listInvites() {
    return apiFetch<Invite[]>("/org/invites");
  },

  revokeInvite(inviteId: string) {
    return apiFetch<void>(`/org/invites/${inviteId}`, { method: "DELETE" });
  },

  createApiKey(name: string, role: RoleInput = "viewer", expiresInDays?: number) {
    return apiFetch<ApiKeyCreated>("/org/api-keys", {
      method: "POST",
      body: JSON.stringify({ name, role, expires_in_days: expiresInDays ?? null }),
    });
  },

  listApiKeys() {
    return apiFetch<ApiKey[]>("/org/api-keys");
  },

  revokeApiKey(keyId: string) {
    return apiFetch<void>(`/org/api-keys/${keyId}`, { method: "DELETE" });
  },

  getUsage() {
    return apiFetch<UsageSummary>("/org/usage");
  },

  listAuditLogs(filters: AuditLogFilters = {}) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== "") params.append(key, String(value));
    });
    const query = params.toString();
    return apiFetch<AuditLogPage>(`/org/audit-logs${query ? `?${query}` : ""}`);
  },

  async login(email: string, password: string) {
    const body = new URLSearchParams();
    body.append("username", email);
    body.append("password", password);

    const response = await fetch(`${API_V1}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });

    if (!response.ok) {
      throw await errorFromResponse(response, "Incorrect email or password.");
    }

    const data = await response.json();
    setToken(data.access_token);
    if (data.refresh_token) setRefreshToken(data.refresh_token);
    return data;
  },

  logout() {
    const accessToken = getToken();
    const refreshToken = getRefreshToken();
    clearToken();
    if (!accessToken && !refreshToken) return;
    try {
      void fetch(`${API_V1}/auth/logout`, {
        method: "POST",
        keepalive: true,
        headers: {
          "Content-Type": "application/json",
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify({ refresh_token: refreshToken }),
      }).catch(() => {
        /* signed out locally regardless */
      });
    } catch {
      /* no fetch available (tests, old browsers): signed out locally */
    }
  },


  listProjects() {
    return apiFetch<Project[]>("/projects/");
  },

  createProject(name: string) {
    return apiFetch<Project>("/projects/", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },


  listDocuments(projectId: string) {
    return apiFetch<DocumentItem[]>(`/documents/?project_id=${projectId}`);
  },

  async uploadDocuments(
    projectId: string,
    files: File[],
    chunkStrategy = "sentence",
    chunkSize = 400,
    chunkOverlap = 60
  ) {
    const form = new FormData();
    form.append("project_id", projectId);
    form.append("chunk_strategy", chunkStrategy);
    form.append("chunk_size", String(chunkSize));
    form.append("chunk_overlap", String(chunkOverlap));

    files.forEach((file) => form.append("files", file));

    const send = () => {
      const token = getToken();
      return fetch(`${API_V1}/documents/upload`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
    };

    let response = await send();
    if (response.status === 401) {
      if (!(await refreshSession())) endSession();
      response = await send();
      if (response.status === 401) endSession();
    }

    if (!response.ok) {
      throw await errorFromResponse(response, "Upload failed.");
    }

    return response.json() as Promise<DocumentItem[]>;
  },


  search(query: string, projectId?: string, topK?: number, documentIds?: string[]) {
    return apiFetch<SearchResponse>("/search/", {
      method: "POST",
      body: JSON.stringify({
        query,
        project_id: projectId,
        top_k: topK,
        ...(documentIds && documentIds.length > 0 ? { document_ids: documentIds } : {}),
      }),
    });
  },


  createChatSession(projectId: string, title?: string) {
    return apiFetch<ChatSessionItem>("/chat/sessions", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, title }),
    });
  },

  listChatSessions() {
    return apiFetch<ChatSessionItem[]>("/chat/sessions");
  },

  listMessages(sessionId: string) {
    return apiFetch<ChatMessageItem[]>(`/chat/sessions/${sessionId}/messages`);
  },

  askQuestion(sessionId: string, message: string, mode?: ChatMode, documentIds?: string[]) {
    return apiFetch<ChatResponse>(`/chat/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        message,
        ...(mode ? { mode } : {}),
        ...(documentIds && documentIds.length > 0 ? { document_ids: documentIds } : {}),
      }),
    });
  },

  deleteChatSession(sessionId: string) {
    return apiFetch<void>(`/chat/sessions/${sessionId}`, { method: "DELETE" });
  },

  sendFeedback(messageId: string, rating: number, comment?: string) {
    return apiFetch<{ status: string }>(`/chat/messages/${messageId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ rating, comment }),
    });
  },



  getProject(projectId: string) {
    return apiFetch<ProjectDetail>(`/projects/${projectId}`);
  },

  renameProject(projectId: string, name: string) {
    return apiFetch<Project>(`/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
  },

  deleteProject(projectId: string) {
    return apiFetch<void>(`/projects/${projectId}`, { method: "DELETE" });
  },


  renameDocument(documentId: string, filename: string) {
    return apiFetch<DocumentItem>(`/documents/${documentId}`, {
      method: "PATCH",
      body: JSON.stringify({ filename }),
    });
  },

  deleteDocument(documentId: string) {
    return apiFetch<void>(`/documents/${documentId}`, { method: "DELETE" });
  },

  reindexDocument(documentId: string, options: ReindexOptions = {}) {
    return apiFetch<DocumentItem>(`/documents/${documentId}/reindex`, {
      method: "POST",
      body: JSON.stringify(options),
    });
  },

  documentChunks(documentId: string, aroundChunkId?: string, window = 2) {
    const params = new URLSearchParams({ window: String(window) });
    if (aroundChunkId) params.set("around", aroundChunkId);
    return apiFetch<ChunkContext[]>(`/documents/${documentId}/chunks?${params}`);
  },

  async fetchDocumentFile(documentId: string): Promise<ArrayBuffer> {
    const send = () => {
      const token = getToken();
      return fetch(`${API_V1}/documents/${documentId}/preview`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
    };
    let response = await send();
    if (response.status === 401) {
      if (!(await refreshSession())) endSession();
      response = await send();
    }
    if (!response.ok) throw await errorFromResponse(response, "Could not open the file.");
    return response.arrayBuffer();
  },

  uploadFile(
    projectId: string,
    file: File,
    onProgress?: (fraction: number) => void,
    chunkStrategy = "sentence",
    chunkSize = 400,
    chunkOverlap = 60
  ): Promise<DocumentItem[]> {
    const form = new FormData();
    form.append("project_id", projectId);
    form.append("chunk_strategy", chunkStrategy);
    form.append("chunk_size", String(chunkSize));
    form.append("chunk_overlap", String(chunkOverlap));
    form.append("files", file);

    const send = () =>
      new Promise<{ status: number; body: string; retryAfter: string | null }>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_V1}/documents/upload`);
        const token = getToken();
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
        };
        xhr.onload = () =>
          resolve({
            status: xhr.status,
            body: xhr.responseText,
            retryAfter: xhr.getResponseHeader("Retry-After"),
          });
        xhr.onerror = () => reject(new Error("Network error while uploading."));
        xhr.send(form);
      });

    return (async () => {
      let result = await send();
      if (result.status === 401) {
        if (!(await refreshSession())) endSession();
        result = await send();
      }
      const response = new Response(result.status === 204 ? null : result.body, {
        status: result.status,
        headers: result.retryAfter ? { "Retry-After": result.retryAfter } : {},
      });
      if (!response.ok) throw await errorFromResponse(response, "Upload failed.");
      onProgress?.(1);
      return (await response.json()) as DocumentItem[];
    })();
  },


  listProjectChatSessions(projectId: string) {
    return apiFetch<ChatSessionItem[]>(`/chat/sessions?project_id=${projectId}`);
  },

  renameChatSession(sessionId: string, title: string) {
    return apiFetch<ChatSessionItem>(`/chat/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  },

  async streamQuestion(
    sessionId: string,
    message: string,
    options: AskOptions = {},
    handlers: StreamHandlers = {}
  ): Promise<ChatResponse> {
    const body = JSON.stringify({
      message,
      ...(options.mode ? { mode: options.mode } : {}),
      ...(options.documentIds && options.documentIds.length > 0
        ? { document_ids: options.documentIds }
        : {}),
    });
    const send = () => {
      const token = getToken();
      return fetch(`${API_V1}/chat/sessions/${sessionId}/messages/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body,
        signal: options.signal,
      });
    };

    let response = await send();
    if (response.status === 401) {
      if (!(await refreshSession())) endSession();
      response = await send();
      if (response.status === 401) endSession();
    }
    if (!response.ok || !response.body) {
      throw await errorFromResponse(response, "Could not get an answer.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const parsed = parseSseBuffer(done ? `${buffer}\n\n` : buffer);
      buffer = parsed.rest;
      for (const { event, data } of parsed.events) {
        const payload = JSON.parse(data);
        switch (event) {
          case "start":
            handlers.onStart?.(payload);
            break;
          case "query":
            handlers.onQuery?.(payload);
            break;
          case "step":
            handlers.onStep?.(payload);
            break;
          case "citations":
            handlers.onCitations?.(payload.citations);
            break;
          case "token":
            handlers.onToken?.(payload.text);
            break;
          case "reset":
            handlers.onReset?.();
            break;
          case "error":
            throw new ApiError(payload.detail || "Could not get an answer.", 500);
          case "done":
            return payload as ChatResponse;
        }
      }
      if (done) break;
    }
    throw new Error("The answer was interrupted. Please try again.");
  },


  getEmbeddingMap(projectId: string) {
    return apiFetch<EmbeddingMap>(`/projects/${projectId}/embedding-map`);
  },


  listWebhooks() {
    return apiFetch<Webhook[]>("/webhooks");
  },

  listWebhookEvents() {
    return apiFetch<WebhookEventType[]>("/webhooks/events");
  },

  createWebhook(url: string, eventTypes: string[], description?: string) {
    return apiFetch<WebhookCreated>("/webhooks", {
      method: "POST",
      body: JSON.stringify({ url, event_types: eventTypes, description: description || null }),
    });
  },

  deleteWebhook(webhookId: string) {
    return apiFetch<void>(`/webhooks/${webhookId}`, { method: "DELETE" });
  },

  testWebhook(webhookId: string) {
    return apiFetch<WebhookDelivery>(`/webhooks/${webhookId}/test`, { method: "POST" });
  },

  listWebhookDeliveries(webhookId: string, limit = 20) {
    return apiFetch<WebhookDelivery[]>(`/webhooks/${webhookId}/deliveries?limit=${limit}`);
  },


  getFeedbackSummary(days = 30) {
    return apiFetch<FeedbackSummary>(`/org/feedback?days=${days}`);
  },


  createShareLink(kind: ShareKind, targetId: string, expiresInDays?: number | null) {
    return apiFetch<ShareLinkCreated>("/shares", {
      method: "POST",
      body: JSON.stringify({
        kind,
        target_id: targetId,
        ...(expiresInDays ? { expires_in_days: expiresInDays } : {}),
      }),
    });
  },

  listShareLinks(kind: ShareKind, targetId: string) {
    return apiFetch<ShareLink[]>(`/shares?kind=${kind}&target_id=${encodeURIComponent(targetId)}`);
  },

  revokeShareLink(shareId: string) {
    return apiFetch<void>(`/shares/${shareId}`, { method: "DELETE" });
  },

  async getPublicShare(token: string) {
    const response = await fetch(`${API_V1}/public/shares/${encodeURIComponent(token)}`);
    if (!response.ok) throw await errorFromResponse(response, "This link could not be opened.");
    return (await response.json()) as PublicShare;
  },


  listPrompts() {
    return apiFetch<PromptTemplate[]>("/prompts");
  },

  createPrompt(title: string, body: string, scope: PromptScope = "personal") {
    return apiFetch<PromptTemplate>("/prompts", {
      method: "POST",
      body: JSON.stringify({ title, body, scope }),
    });
  },

  deletePrompt(promptId: string) {
    return apiFetch<void>(`/prompts/${promptId}`, { method: "DELETE" });
  },


  compareDocuments(documentA: string, documentB: string, focus?: string) {
    return apiFetch<Comparison>("/compare", {
      method: "POST",
      body: JSON.stringify({
        document_a: documentA,
        document_b: documentB,
        ...(focus?.trim() ? { focus: focus.trim() } : {}),
      }),
    });
  },


  health() {
    return apiFetch<{
      status: string;
      llm: { provider: string; model: string; api_key_configured: boolean };
      embeddings: { provider: string; dimension: number };
    }>("/health");
  },
};
