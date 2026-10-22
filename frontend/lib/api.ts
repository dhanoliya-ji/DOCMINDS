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
 */

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

// The localStorage key under which we save the token.
const TOKEN_KEY = "edia_token";

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
async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
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

  // 401 means the token is missing, invalid or expired. Clear it and send the
  // user back to the login page, rather than leaving them on a screen that
  // silently fails to load anything.
  if (response.status === 401) {
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

  if (!response.ok) {
    // FastAPI reports problems as {"detail": "..."}. Reading it gives the user
    // the real reason ("Project not found") instead of a bare status code.
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body.detail) {
        // Validation errors arrive as an ARRAY of problems rather than a
        // string, so handle both shapes.
        message =
          typeof body.detail === "string"
            ? body.detail
            : body.detail[0]?.msg || message;
      }
    } catch {
      // The error body was not JSON at all -- keep the generic message.
    }
    throw new Error(message);
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

export interface Project {
  id: string;
  name: string;
  org_id: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentItem {
  id: string;
  filename: string;
  file_type: string;
  file_size: number;
  // "pending" -> "processing" -> "completed" | "failed"
  status: string;
  version: number;
  meta_data: DocumentMeta;
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
  created_at: string;
}

export interface ChatSessionItem {
  id: string;
  title: string;
  project_id: string;
  user_id: string;
  created_at: string;
  updated_at: string;
}

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

/**
 * One tool call the agent made while researching an answer.
 *
 * Only ever filled in agent mode, and only on the LIVE response: the backend
 * does not save steps, so a reloaded conversation has none.
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
}

// ======================================================================
// THE API -- one function per backend endpoint
// ======================================================================

export const api = {
  // ---- Authentication ----------------------------------------------

  /**
   * Create an account and a brand-new organization for it.
   * The first user of an organization automatically becomes its Admin.
   */
  async signup(email: string, password: string, orgName: string) {
    return apiFetch<{ id: string; email: string; role: string }>(
      "/auth/signup",
      {
        method: "POST",
        body: JSON.stringify({
          email,
          password,
          org_name: orgName,
        }),
      }
    );
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
      throw new Error("Incorrect email or password.");
    }

    const data = await response.json();
    setToken(data.access_token);
    return data;
  },

  logout() {
    clearToken();
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

    const token = getToken();
    const response = await fetch(`${API_V1}/documents/upload`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });

    if (!response.ok) {
      let message = "Upload failed.";
      try {
        const body = await response.json();
        if (body.detail) message = String(body.detail);
      } catch {
        /* the error body was not JSON */
      }
      throw new Error(message);
    }

    return response.json() as Promise<DocumentItem[]>;
  },

  // ---- Semantic search -----------------------------------------------

  search(query: string, projectId?: string, topK?: number) {
    return apiFetch<SearchResponse>("/search/", {
      method: "POST",
      body: JSON.stringify({
        query,
        project_id: projectId,
        top_k: topK,
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
  askQuestion(sessionId: string, message: string, mode?: ChatMode) {
    return apiFetch<ChatResponse>(`/chat/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify(mode ? { message, mode } : { message }),
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
