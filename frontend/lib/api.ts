/**
 * api.ts
 * ======
 * WHAT THIS FILE DOES
 * -------------------
 * Every single conversation between the browser and the Python backend goes
 * through this file. Nothing else in the frontend calls `fetch()` directly.
 *
 * WHY CENTRALISE IT?
 * ------------------
 * Three things must happen on virtually every request:
 *   1. Prefix the URL with the backend address.
 *   2. Attach the login token, so the backend knows who is asking.
 *   3. Turn a failed response into a readable error message.
 *
 * Writing those three steps in thirty different components would mean thirty
 * chances to forget one. Here they are written once.
 *
 * HOW LOGIN WORKS IN THIS APP
 * ---------------------------
 * When you log in, the backend returns a JWT -- a long signed string proving
 * who you are. We keep it in `localStorage` (the browser's small persistent
 * key/value store) so a page refresh does not log you out. Every later request
 * carries it in the `Authorization: Bearer <token>` header.
 *
 * The access token lasts 30 minutes. Login also returns a REFRESH token (7
 * days), stored alongside it. When a request comes back 401, `apiFetch`
 * quietly trades the refresh token for a fresh pair and retries once; only if
 * that fails is the user sent back to /login. Signing out tells the backend
 * to revoke both tokens, so a copied token stops working too.
 */

import type { components } from "@/lib/api-types";

// ----------------------------------------------------------------------
// WHERE THE BACKEND LIVES
// ----------------------------------------------------------------------
// `NEXT_PUBLIC_` is a Next.js convention: only variables with that prefix are
// sent to the browser. That is deliberate -- it prevents a server-side secret
// from being bundled into public JavaScript by accident.
//
// Locally this is http://localhost:8000. In production it is set to the
// deployed Render URL in the hosting dashboard.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const API_V1 = `${API_BASE}/api/v1`;

// The localStorage keys under which we save the two tokens.
const TOKEN_KEY = "edia_token";
const REFRESH_KEY = "edia_refresh";

// ----------------------------------------------------------------------
// TOKEN STORAGE
// ----------------------------------------------------------------------

export function getToken(): string | null {
  // Next.js renders pages on the SERVER first, where `window` does not exist.
  // Touching localStorage there would crash the render, so we check first.
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
  // Both go together: a refresh token left behind could sign the user
  // straight back in after they chose to sign out.
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

// ----------------------------------------------------------------------
// SILENT REFRESH
// ----------------------------------------------------------------------

// The refresh currently in progress, shared by everyone who needs it.
//
// WHY THIS MATTERS: the backend ROTATES refresh tokens, and treats a token
// presented twice as stolen -- it then ends the whole session. A page that
// fires three requests at once, all of which come back 401, would otherwise
// send three refreshes with the same token, and the second would log the user
// out. Sharing one promise means one refresh, whatever the number of callers.
let refreshInFlight: Promise<boolean> | null = null;

/** Trade the stored refresh token for a new pair. Resolves true on success. */
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

/** Clear the session and send the user to sign in again. */
function endSession(): never {
  clearToken();
  if (typeof window !== "undefined") {
    // A HARD navigation, deliberately, and not `router.push`.
    //
    // Two reasons. This is a plain module function rather than a component,
    // so there is no router to reach for -- and a full page load is what we
    // actually want: it discards every piece of in-memory React state along
    // with the token. A soft navigation would carry the previous user's
    // loaded projects and open chat session into the login screen.
    //
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login";
  }
  throw new Error("Your session expired. Please sign in again.");
}

// ----------------------------------------------------------------------
// ERRORS THAT REMEMBER THEIR STATUS
// ----------------------------------------------------------------------

/**
 * A failed request, carrying the HTTP status and any Retry-After wait.
 *
 * WHY NOT A PLAIN Error?
 * ----------------------
 * Most screens only need the message, and an ApiError IS an Error, so they
 * keep working unchanged. But a few need to know WHICH failure it was: the
 * upload zone words a 413 differently from a 415, and chat and search want to
 * say "try again in 40 seconds" after a 429. Parsing that back out of the
 * message text would break the first time the backend rewords a sentence.
 */
export class ApiError extends Error {
  readonly status: number;
  /** Seconds to wait before retrying (from Retry-After), or null. */
  readonly retryAfter: number | null;

  constructor(message: string, status: number, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/**
 * Read a Retry-After header as a number of seconds.
 *
 * The header may be either a number of seconds or an HTTP date; the backend
 * only sends seconds, but a proxy in front of it could send a date. NOTE: in a
 * browser this header is only readable cross-origin because the backend lists
 * it in CORS `expose_headers` -- without that, `get` returns null here.
 */
function readRetryAfter(response: Response): number | null {
  // `headers?.get` because test doubles (and very old polyfills) may not
  // provide a Headers object at all.
  const raw = response.headers?.get?.("Retry-After");
  if (!raw) return null;
  const seconds = Number(raw);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds);
  const date = Date.parse(raw);
  if (Number.isNaN(date)) return null;
  return Math.max(0, Math.ceil((date - Date.now()) / 1000));
}

/**
 * Turn a non-2xx response into an ApiError with the backend's own words.
 *
 * FastAPI reports problems as {"detail": "..."}. Reading it gives the user the
 * real reason ("Project not found") instead of a bare status code. Validation
 * errors arrive as an ARRAY of problems rather than a string, and String() of
 * that would show "[object Object]", so both shapes are handled.
 */
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

/** 42 -> "42 seconds", 150 -> "3 minutes", 7200 -> "2 hours" (as the backend words it). */
export function formatWait(seconds: number): string {
  if (seconds < 90) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  if (seconds < 90 * 60) return `${Math.ceil(seconds / 60)} minutes`;
  if (seconds < 48 * 3600) return `${Math.ceil(seconds / 3600)} hours`;
  return `${Math.ceil(seconds / 86400)} days`;
}

// ----------------------------------------------------------------------
// THE CORE REQUEST FUNCTION
// ----------------------------------------------------------------------

/**
 * Make an authenticated JSON request to the backend.
 *
 * `<T>` is a generic type parameter: the caller declares what shape of data it
 * expects back, and TypeScript then checks the rest of their code against it.
 * For example `apiFetch<Project[]>("/projects/")` yields a typed array.
 */
export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  // True on the one retry after a silent refresh, so a second 401 ends the
  // session instead of looping.
  isRetry = false
): Promise<T> {
  const token = getToken();

  // Build the headers. `...options.headers` spreads any caller-supplied
  // headers in, so a caller can override these defaults when needed.
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  };

  // Only attach the Authorization header when we actually have a token,
  // otherwise the login and signup endpoints would receive a useless
  // "Bearer null".
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_V1}${path}`, { ...options, headers });

  // 401 means the token is missing, invalid or expired. First try a silent
  // refresh and repeat the request once. If that is not possible, clear the
  // tokens and send the user back to the login page, rather than leaving them
  // on a screen that silently fails to load anything.
  if (response.status === 401) {
    if (!isRetry && (await refreshSession())) {
      return apiFetch<T>(path, options, true);
    }
    endSession();
  }

  if (!response.ok) {
    throw await errorFromResponse(response, `Request failed (${response.status})`);
  }

  // 204 No Content has an empty body, so calling .json() on it would throw.
  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

/**
 * Turn an unknown thrown value into a readable message.
 *
 * TypeScript types everything in a `catch` block as `unknown`, because JS lets
 * you throw literally any value - a string, a number, anything. This narrows it
 * safely instead of assuming it is always an Error.
 */
export function getErrorMessage(error: unknown, fallback = "Something went wrong."): string {
  if (error instanceof ApiError && error.message) {
    // A 429 (rate limit, or the monthly token quota) with a Retry-After: make
    // sure the user is told WHEN to try again. The backend's rate-limit text
    // already says so; the hint is only added when the message does not.
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

/**
 * Facts the ingestion pipeline records about a document or chunk.
 *
 * Every field is optional because they are filled in progressively: a document
 * still queued has almost none of them, and a failed one has `error` instead.
 * The index signature allows extra keys the backend may add later without
 * requiring a frontend change.
 */
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

// ======================================================================
// TYPES -- the shapes of data the backend sends us
// ======================================================================
// These mirror the Pydantic schemas in backend/app/schemas/. Keeping them in
// step means TypeScript catches a mismatch at build time rather than the user
// discovering it as `undefined` on screen.
//
// GENERATED TYPES
// Where the backend's schema says exactly what the screens rely on, the type
// is taken straight from lib/api-types.ts -- generated from openapi.json by
// `npm run gen:api` (see docs/DEVELOPMENT.md) -- so a backend change shows up
// here as a compile error rather than a surprise. The hand-written ones below
// stay where they are STRICTER than the schema says (a role as a union of
// three names rather than any string, say) or describe something the schema
// cannot (the SSE stream, the progressive meta_data).

/** A schema from the generated OpenAPI types, by its backend name. */
export type Schema<Name extends keyof components["schemas"]> = components["schemas"][Name];

export type Project = Schema<"ProjectOut">;

export interface DocumentItem {
  id: string;
  filename: string;
  file_type: string;
  file_size: number;
  // "pending" -> "processing" -> "completed" | "failed"
  // A temporary failure (a network blip, a rate limit) sends a document back
  // to "pending" to be retried, with the reason in `error_message`.
  status: string;
  version: number;
  meta_data: DocumentMeta;
  // Why processing failed -- or, while still "pending", why it is being
  // retried. Null when nothing went wrong. Optional so that responses from an
  // older backend, which only had meta_data.error, still type-check.
  error_message?: string | null;
  // How many times processing has started; above 1 means it was retried.
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
  // Similarity from 0 to 1. Higher is a closer match in meaning.
  score: number;
  meta_data: DocumentMeta;
  // Which half of hybrid search found it. Absent when hybrid search is off.
  match_type?: "vector" | "keyword" | "both" | null;
}

export interface SearchResponse {
  query: string;
  results: SearchResultItem[];
  count: number;
  took_seconds: number;
}

export interface CitationItem {
  // Matches the [1], [2] markers the AI writes in its answer.
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
  role: string; // "user" | "assistant"
  content: string;
  tokens_used: number | null;
  latency: number | null;
  citations: CitationItem[];
  // How an ASSISTANT answer was produced, saved since batch 2 so a resumed
  // conversation renders exactly like the live one. Optional: older answers
  // (and older backends) have none.
  mode?: ChatMode | null;
  model?: string | null;
  steps?: AgentStep[];
  created_at: string;
}

export type ChatSessionItem = Schema<"ChatSessionOut">;

/**
 * HOW THE BACKEND SHOULD ANSWER A QUESTION
 * ----------------------------------------
 *   "rag"   -- the fixed pipeline: embed the question, fetch the closest
 *              chunks once, make ONE language-model call. Fast and cheap.
 *   "agent" -- a Pydantic AI agent that decides for itself which tools to
 *              call (search, list documents, read a section...) and may search
 *              several times before answering. Slower, but it copes with
 *              multi-part and multi-document questions.
 */
export type ChatMode = "rag" | "agent";

// ----------------------------------------------------------------------
// ACCOUNTS AND ORGANIZATION ADMIN (backend/app/schemas/account.py)
// ----------------------------------------------------------------------

// Stored role names, and what they mean: Employee = viewer (read, search,
// chat), Manager = editor (+ upload, projects), Admin = admin (+ members,
// invites, API keys, audit log). The backend also accepts the aliases.
export type Role = "Admin" | "Manager" | "Employee";
export type RoleInput = Role | "admin" | "editor" | "viewer";

export interface Member {
  id: string;
  email: string;
  role: Role;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
}

export interface Invite {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  invited_by: string | null;
  created_at: string;
  status: "pending" | "accepted" | "revoked" | "expired";
}

/** Only the create response carries the token -- it is never shown again. */
export interface InviteCreated extends Invite {
  token: string;
  invite_url: string;
}

export interface InvitePreview {
  email: string;
  role: Role;
  org_name: string;
  expires_at: string;
}

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

/** Only the create response carries the key -- it is never shown again. */
export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export interface AuditLogItem {
  id: string;
  action: string;
  details: Record<string, unknown>;
  ip_address: string | null;
  user_id: string | null;
  created_at: string;
}

export interface AuditLogPage {
  items: AuditLogItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface AuditLogFilters {
  limit?: number;
  offset?: number;
  /** Exact action, or a prefix ending in "." such as "member." */
  action?: string;
  user_id?: string;
  since?: string;
  until?: string;
}

// ----------------------------------------------------------------------
// THE SIGNED-IN USER AND THE ORGANIZATION'S USAGE
// ----------------------------------------------------------------------

/** GET /users/me (backend/app/schemas/user.py UserOut). */
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

/** One metered quantity against its limit (backend/app/schemas/usage.py). */
export interface QuotaUsage {
  used: number;
  quota: number;
  // True when the quota is 0, which the backend treats as "no limit".
  unlimited: boolean;
  remaining: number | null;
}

/** GET /org/usage (backend/app/schemas/usage.py UsageSummary). */
export interface UsageSummary {
  period_start: string;
  period_end: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_usd: number;
  tokens: QuotaUsage;
  documents: QuotaUsage;
  storage_bytes: QuotaUsage;
  by_model: {
    model: string;
    requests: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    estimated_cost_usd: number;
  }[];
  by_mode: { mode: string; requests: number; total_tokens: number }[];
}

/**
 * One tool call the agent made while researching an answer.
 *
 * Only ever filled in agent mode. Saved with the answer (since batch 2), so
 * a conversation reopened from the history sidebar shows them too.
 */
export interface AgentStep {
  // The tool's name, e.g. "search_documents".
  tool: string;
  // The arguments the model chose, e.g. { query: "annual leave" }.
  args: Record<string, unknown>;
  // A short human-readable result, e.g. "Found 5 excerpts from 2 documents".
  summary: string;
}

export interface ChatResponse {
  session_id: string;
  user_message: ChatMessageItem;
  assistant_message: ChatMessageItem;
  citations: CitationItem[];
  model: string;
  // Which mode actually answered. Optional so an older backend that does not
  // send it still type-checks; treat a missing value as "rag".
  mode?: ChatMode;
  // The agent's tool calls, in order. Empty (or absent) in rag mode.
  steps?: AgentStep[];
  // What retrieval actually searched for -- differs from the question when
  // a follow-up was rewritten into a standalone query. Rag mode only.
  search_query?: string | null;
}

// ----------------------------------------------------------------------
// BATCH 2: projects, documents, streaming, history, feedback
// ----------------------------------------------------------------------

/** GET /projects/{id} -- one project plus how much is in it. */
export type ProjectDetail = Schema<"ProjectDetail">;

/** One chunk of a document's text, for the citation viewer. */
export type ChunkContext = Schema<"ChunkContextOut">;

/** New chunking options for a re-index; anything left out is kept. */
export interface ReindexOptions {
  chunk_strategy?: "fixed_size" | "sentence" | "markdown";
  chunk_size?: number;
  chunk_overlap?: number;
}

/** Up and down votes for one slice of answers (a mode, or a model). */
export type FeedbackBreakdown = Schema<"FeedbackBreakdown">;

// ---- The 3D embedding map (GET /projects/{id}/embedding-map) ----------
export type EmbeddingMap = Schema<"EmbeddingMap">;
export type EmbeddingPoint = Schema<"EmbeddingPoint">;

// ---- Webhooks (admin; the routes 404 when WEBHOOKS_ENABLED is off) ----
export type Webhook = Schema<"WebhookOut">;
/** Create and rotate responses only: the signing secret, shown once. */
export type WebhookCreated = Schema<"WebhookCreated">;
export type WebhookDelivery = Schema<"WebhookDeliveryOut">;
export type WebhookEventType = Schema<"WebhookEventType">;

/** GET /org/feedback (backend/app/schemas/rag.py FeedbackSummary). */
export interface FeedbackSummary {
  total: number;
  up: number;
  down: number;
  // up / (up + down); null before anyone has voted.
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

/** Options for asking a question, by either endpoint. */
export interface AskOptions {
  mode?: ChatMode;
  // Answer only from these documents. Empty or absent = the whole project.
  documentIds?: string[];
  // Lets the caller stop a streaming answer (the "Stop" button).
  signal?: AbortSignal;
}

/**
 * Callbacks for a STREAMED answer, one per kind of event the backend sends
 * (see "THE STREAM, EVENT BY EVENT" in backend/app/api/v1/chat.py).
 */
export interface StreamHandlers {
  onStart?: (event: { session_id: string; user_message_id: string; mode: ChatMode; title: string }) => void;
  onQuery?: (event: { search_query: string; rewritten: boolean }) => void;
  onStep?: (step: AgentStep) => void;
  onCitations?: (citations: CitationItem[]) => void;
  onToken?: (text: string) => void;
}

/** One parsed Server-Sent Event. */
export interface SseEvent {
  event: string;
  data: string;
}

/**
 * Split a buffer of SSE text into complete events plus the unfinished rest.
 *
 * A network read can end anywhere -- in the middle of a line, even in the
 * middle of a UTF-8 character (the decoder handles that part). An event is
 * only complete once its blank line has arrived, so anything after the last
 * blank line is handed back as `rest` and glued to the front of the next
 * read. Exported so the tests can feed it awkward splits directly.
 */
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

// ======================================================================
// THE API -- one function per backend endpoint
// ======================================================================

export const api = {
  // ---- Authentication ----------------------------------------------

  /**
   * Create an account.
   *
   * Without an invite token this creates a brand-new organization and makes
   * the user its Admin. With one (from an invite email's `?invite=` link) it
   * joins the inviting organization with the role the Admin chose; the email
   * must be the one the invite was sent to.
   */
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

  /** What an invite link is for, so a signup form can say "Join Acme as Manager". */
  previewInvite(token: string) {
    return apiFetch<InvitePreview>(`/auth/invites/${encodeURIComponent(token)}`);
  },

  /** Always resolves the same way, whether or not the email has an account. */
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

  /** Who is signed in: email, role and whether the email is confirmed. */
  getMe() {
    return apiFetch<CurrentUser>("/users/me");
  },

  // ---- Organization admin (Admin role only) -------------------------

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

  /** This month's tokens, estimated cost and the organization's quotas. */
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

  /**
   * Exchange an email and password for access tokens.
   *
   * NOTE this one endpoint does NOT take JSON. The backend uses FastAPI's
   * standard OAuth2 form, which requires `application/x-www-form-urlencoded`
   * and expects the email under the field name `username`. That is an OAuth2
   * convention, not a mistake.
   */
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
      // The backend's own words: wrong credentials (401), a deactivated
      // account (400), an unconfirmed email (403) or too many attempts (429)
      // each need a different reaction from the user.
      throw await errorFromResponse(response, "Incorrect email or password.");
    }

    const data = await response.json();
    setToken(data.access_token);
    if (data.refresh_token) setRefreshToken(data.refresh_token);
    return data;
  },

  /**
   * Sign out: forget both tokens here AND ask the backend to revoke them.
   *
   * The local part happens first and unconditionally, so the user is signed
   * out on this device even if the network call fails. The backend call is
   * best effort (`keepalive` lets it finish while the page navigates away):
   * it is what makes a COPY of the token stop working too.
   */
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

  // ---- Projects ------------------------------------------------------

  listProjects() {
    return apiFetch<Project[]>("/projects/");
  },

  createProject(name: string) {
    return apiFetch<Project>("/projects/", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },

  // ---- Documents -----------------------------------------------------

  listDocuments(projectId: string) {
    return apiFetch<DocumentItem[]>(`/documents/?project_id=${projectId}`);
  },

  /**
   * Upload one or more files.
   *
   * File uploads use `FormData`, not JSON, because JSON cannot carry binary
   * data. Note we deliberately do NOT set a Content-Type header here: the
   * browser must set it itself, because it has to append a randomly generated
   * "boundary" marker that separates the parts of the request. Setting it
   * manually breaks the upload.
   */
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

    // The backend accepts a list, so every file is appended under the same
    // field name "files".
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
    // Same silent refresh as apiFetch: an upload started just as the access
    // token expired should not fail.
    if (response.status === 401) {
      if (!(await refreshSession())) endSession();
      response = await send();
      if (response.status === 401) endSession();
    }

    if (!response.ok) {
      // The backend says exactly what was wrong: 413 too large, 415 a type it
      // cannot read or a renamed file, 422 bad chunking options, 429 too many
      // uploads, 402 over the organization's document or storage quota. The
      // status travels with the error so UploadZone can word each one.
      throw await errorFromResponse(response, "Upload failed.");
    }

    return response.json() as Promise<DocumentItem[]>;
  },

  // ---- Semantic search -----------------------------------------------

  search(query: string, projectId?: string, topK?: number, documentIds?: string[]) {
    return apiFetch<SearchResponse>("/search/", {
      method: "POST",
      body: JSON.stringify({
        query,
        project_id: projectId,
        top_k: topK,
        // Only these documents. Sent only when the filter is in use, so an
        // unfiltered search is exactly the request it always was.
        ...(documentIds && documentIds.length > 0 ? { document_ids: documentIds } : {}),
      }),
    });
  },

  // ---- RAG chat --------------------------------------------------------

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

  /**
   * Ask a question. The backend runs the whole pipeline before replying.
   *
   * `mode` picks HOW it answers (see ChatMode above). It is added to the body
   * only when the caller passes one, so a plain call sends exactly
   * `{ "message": "..." }` -- the request the backend has always accepted, and
   * which it treats as "rag".
   */
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


  // ---- Projects: one, rename, delete (batch 2) ------------------------

  getProject(projectId: string) {
    return apiFetch<ProjectDetail>(`/projects/${projectId}`);
  },

  renameProject(projectId: string, name: string) {
    return apiFetch<Project>(`/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
  },

  /** Deletes the project's documents, files and conversations too. */
  deleteProject(projectId: string) {
    return apiFetch<void>(`/projects/${projectId}`, { method: "DELETE" });
  },

  // ---- Documents: rename, delete, re-index, text, file ------------------

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

  /** The cited chunk and `window` neighbours either side (or the start). */
  documentChunks(documentId: string, aroundChunkId?: string, window = 2) {
    const params = new URLSearchParams({ window: String(window) });
    if (aroundChunkId) params.set("around", aroundChunkId);
    return apiFetch<ChunkContext[]>(`/documents/${documentId}/chunks?${params}`);
  },

  /**
   * The original file's bytes, through the authenticated /preview endpoint.
   *
   * Not a plain link: the browser would not send the Authorization header,
   * so the PDF viewer fetches the bytes itself and hands them to pdf.js.
   */
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

  /**
   * Upload ONE file, reporting progress as it goes.
   *
   * WHY XMLHttpRequest AND NOT fetch: fetch has no upload-progress event.
   * XHR's `upload.onprogress` is still the only way a browser reports how
   * many bytes of a request body have been sent -- which is what turns a
   * spinner into "report.pdf 63%".
   *
   * The bulk upload zone calls this once per file, so one bad file is one
   * failed row instead of a refused batch.
   */
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
      // Reuse errorFromResponse by wrapping the XHR result in a Response.
      const response = new Response(result.status === 204 ? null : result.body, {
        status: result.status,
        headers: result.retryAfter ? { "Retry-After": result.retryAfter } : {},
      });
      if (!response.ok) throw await errorFromResponse(response, "Upload failed.");
      onProgress?.(1);
      return (await response.json()) as DocumentItem[];
    })();
  },

  // ---- Conversation history ---------------------------------------------

  /** This user's conversations about one project, most recent first. */
  listProjectChatSessions(projectId: string) {
    return apiFetch<ChatSessionItem[]>(`/chat/sessions?project_id=${projectId}`);
  },

  renameChatSession(sessionId: string, title: string) {
    return apiFetch<ChatSessionItem>(`/chat/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  },

  /**
   * Ask a question and receive the answer AS IT IS WRITTEN.
   *
   * The backend replies with Server-Sent Events: the question is saved, the
   * sources arrive, then the answer word by word (and, in agent mode, each
   * research step the moment the agent takes it). Each event is passed to
   * the matching handler; the promise resolves with the final ChatResponse --
   * the same object askQuestion returns -- once the answer has been saved.
   *
   * WHY fetch AND NOT EventSource: EventSource can only make GET requests
   * and cannot send an Authorization header. fetch can do both, and its
   * response body is a stream we read chunk by chunk.
   */
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
    // Ownership, the rate limit and the quota are checked BEFORE the stream
    // starts, so they arrive as ordinary HTTP errors with their status.
    if (!response.ok || !response.body) {
      throw await errorFromResponse(response, "Could not get an answer.");
    }

    const reader = response.body.getReader();
    // `stream: true` keeps a multi-byte character split across two reads
    // intact instead of turning it into two replacement characters.
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

  // ---- The 3D embedding map (search page) ------------------------------

  /** Every chunk of a project as a point in 3D (PCA, computed server-side). */
  getEmbeddingMap(projectId: string) {
    return apiFetch<EmbeddingMap>(`/projects/${projectId}/embedding-map`);
  },

  // ---- Webhooks (Admin) -------------------------------------------------
  // Every route answers 404 while the deployment has webhooks switched off,
  // so the settings screen treats a 404 from listWebhooks as "hide me".

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

  /** Queue a "ping" delivery to one endpoint. */
  testWebhook(webhookId: string) {
    return apiFetch<WebhookDelivery>(`/webhooks/${webhookId}/test`, { method: "POST" });
  },

  listWebhookDeliveries(webhookId: string, limit = 20) {
    return apiFetch<WebhookDelivery[]>(`/webhooks/${webhookId}/deliveries?limit=${limit}`);
  },

  // ---- Answer quality (Admin) -------------------------------------------

  /** Thumbs up / down over the last `days` days, summarised. */
  getFeedbackSummary(days = 30) {
    return apiFetch<FeedbackSummary>(`/org/feedback?days=${days}`);
  },

  // ---- Health ----------------------------------------------------------

  /**
   * Report backend status and AI configuration.
   * The UI uses this to show a banner when no GROQ_API_KEY is configured, so
   * the user understands why answers look like raw excerpts.
   */
  health() {
    return apiFetch<{
      status: string;
      llm: { provider: string; model: string; api_key_configured: boolean };
      embeddings: { provider: string; dimension: number };
    }>("/health");
  },
};
