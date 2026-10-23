/**
 * DocMinds TypeScript SDK
 * =======================
 * A thin, dependency-free client over `fetch`, typed with the schema
 * generated from the API's OpenAPI document (src/schema.ts, regenerated
 * with `npm run gen` from ../../frontend/openapi.json).
 *
 *     const client = new DocMinds("https://docminds-api.onrender.com", { apiKey });
 *     const projects = await client.listProjects();
 *
 * Works anywhere `fetch`, `FormData` and `Blob` exist: Node 18+, Deno, Bun,
 * edge runtimes. Keep API keys on the server; never ship one to a browser.
 */

import type { components } from "./schema";

export type Schemas = components["schemas"];
export type Project = Schemas["ProjectOut"];
export type Document = Schemas["DocumentOut"];
export type SearchResponse = Schemas["SearchResponse"];
export type ChatSession = Schemas["ChatSessionOut"];
export type ChatResponse = Schemas["ChatResponse"];
export type User = Schemas["UserOut"];

/** A non-2xx answer. `retryAfter` is set (seconds) on a 429. */
export class DocMindsError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfter: number | null = null,
    readonly requestId: string | null = null
  ) {
    super(message);
    this.name = "DocMindsError";
  }
}

export interface ClientOptions {
  /** An API key (`dm_...`), sent as X-API-Key. */
  apiKey?: string;
  /** Or a bearer access token from /auth/login. */
  accessToken?: string;
  /** Override for tests or custom runtimes. */
  fetch?: typeof fetch;
}

export interface UploadFile {
  name: string;
  data: Blob;
}

export class DocMinds {
  private readonly base: string;
  private readonly headers: Record<string, string>;
  private readonly fetchImpl: typeof fetch;

  constructor(baseUrl: string, options: ClientOptions = {}) {
    this.base = baseUrl.replace(/\/+$/, "") + "/api/v1";
    this.headers = {};
    if (options.apiKey) this.headers["X-API-Key"] = options.apiKey;
    if (options.accessToken) this.headers["Authorization"] = `Bearer ${options.accessToken}`;
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  /** One request; JSON in and out, errors thrown as DocMindsError. */
  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers = { ...this.headers };
    let payload: BodyInit | undefined;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const response = await this.fetchImpl(this.base + path, { method, headers, body: payload });
    if (!response.ok) {
      let detail: unknown = response.statusText;
      try {
        detail = ((await response.json()) as { detail?: unknown }).detail ?? detail;
      } catch {
        /* not JSON */
      }
      const message = typeof detail === "string" ? detail : JSON.stringify(detail);
      const retry = response.headers.get("Retry-After");
      throw new DocMindsError(message, response.status, retry ? Number(retry) : null, response.headers.get("X-Request-ID"));
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  me(): Promise<User> {
    return this.request("GET", "/users/me");
  }

  listProjects(): Promise<Project[]> {
    return this.request("GET", "/projects/");
  }

  createProject(name: string): Promise<Project> {
    return this.request("POST", "/projects/", { name });
  }

  listDocuments(projectId: string): Promise<Document[]> {
    return this.request("GET", `/documents/?project_id=${encodeURIComponent(projectId)}`);
  }

  getDocument(documentId: string): Promise<Document> {
    return this.request("GET", `/documents/${encodeURIComponent(documentId)}`);
  }

  /** Upload files; they come back `pending` and are processed in the background. */
  upload(projectId: string, files: UploadFile[]): Promise<Document[]> {
    const form = new FormData();
    form.append("project_id", projectId);
    for (const file of files) form.append("files", file.data, file.name);
    return this.request("POST", "/documents/upload", form);
  }

  search(
    query: string,
    options: { projectId?: string; topK?: number; documentIds?: string[] } = {}
  ): Promise<SearchResponse> {
    return this.request("POST", "/search/", {
      query,
      project_id: options.projectId,
      top_k: options.topK,
      document_ids: options.documentIds,
    });
  }

  createSession(projectId: string, title?: string): Promise<ChatSession> {
    return this.request("POST", "/chat/sessions", { project_id: projectId, title });
  }

  ask(
    sessionId: string,
    message: string,
    options: { mode?: "rag" | "agent"; topK?: number; documentIds?: string[] } = {}
  ): Promise<ChatResponse> {
    return this.request("POST", `/chat/sessions/${encodeURIComponent(sessionId)}/messages`, {
      message,
      mode: options.mode,
      top_k: options.topK,
      document_ids: options.documentIds,
    });
  }
}
