/**
 * api.test.ts
 * ===========
 * WHAT THIS FILE TESTS
 * --------------------
 * `lib/api.ts` -- the single door between this app and the backend.
 *
 * WHY IT IS TESTED FIRST
 * ----------------------
 * Every component depends on it, and it is where the behaviours live that are
 * written once and relied on everywhere: attaching the token, clearing it on a
 * 401, unwrapping FastAPI's error shape, and not calling .json() on an empty
 * body.
 *
 * Each of those fails quietly. A 401 that is not handled centrally does not
 * throw -- it leaves the user on a screen that will not load, with no clue
 * their session expired. An unwrapped error detail does not throw either; it
 * just replaces "Project not found" with "Request failed (404)" at exactly the
 * moment the user needed to be told what went wrong.
 *
 * `fetch` is stubbed, so nothing here touches a network. What is under test is
 * this module's logic, not the backend's.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  clearToken,
  getErrorMessage,
  getRefreshToken,
  getToken,
  setRefreshToken,
  setToken,
  API_BASE,
} from "@/lib/api";

/** Build a Response-shaped object for the stubbed fetch to return. */
function reply(
  status: number,
  body?: unknown,
  { asText = false }: { asText?: boolean } = {}
) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (asText) throw new SyntaxError("Unexpected end of JSON input");
      return body;
    },
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  } as unknown as Response;
}

/** Replace global fetch and hand back the mock for assertions. */
function stubFetch(...responses: Response[]) {
  const mock = vi.fn();
  responses.forEach((r) => mock.mockResolvedValueOnce(r));
  vi.stubGlobal("fetch", mock);
  return mock;
}

/** The options object fetch was called with, on call `n`. */
function callOptions(mock: ReturnType<typeof vi.fn>, n = 0) {
  return mock.mock.calls[n][1] ?? {};
}

function callUrl(mock: ReturnType<typeof vi.fn>, n = 0): string {
  return mock.mock.calls[n][0] as string;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

// ======================================================================
// Token storage
// ======================================================================

describe("token storage", () => {
  it("round-trips a token through localStorage", () => {
    setToken("a-token-value");
    expect(getToken()).toBe("a-token-value");
  });

  it("returns null when nothing is stored", () => {
    expect(getToken()).toBeNull();
  });

  it("clears the token", () => {
    setToken("a-token-value");
    clearToken();
    expect(getToken()).toBeNull();
  });

  it("does not store the token under an obvious key", () => {
    // Not security -- localStorage is readable either way. It is about not
    // advertising the shape of the app's storage to anyone glancing at
    // devtools on a shared screen.
    setToken("a-token-value");
    expect(Object.keys(localStorage)).not.toContain("password");
  });
});

// ======================================================================
// apiFetch: the four behaviours everything depends on
// ======================================================================

describe("authorization header", () => {
  it("is attached when a token exists", async () => {
    setToken("my-token");
    const mock = stubFetch(reply(200, []));

    await api.listProjects();

    expect(callOptions(mock).headers.Authorization).toBe("Bearer my-token");
  });

  it("is omitted entirely when there is no token", async () => {
    // The guard that stops login and signup sending "Bearer null" -- which the
    // backend would try to decode, and reject.
    const mock = stubFetch(reply(200, []));

    await api.listProjects();

    expect(callOptions(mock).headers.Authorization).toBeUndefined();
  });

  it("sends JSON by default", async () => {
    const mock = stubFetch(reply(200, []));

    await api.listProjects();

    expect(callOptions(mock).headers["Content-Type"]).toBe("application/json");
  });
});

describe("a 401 response", () => {
  it("clears the stored token", async () => {
    setToken("an-expired-token");
    stubFetch(reply(401, { detail: "Not authenticated" }));

    await expect(api.listProjects()).rejects.toThrow();

    expect(getToken()).toBeNull();
  });

  it("throws a message about the session, not a status code", async () => {
    setToken("an-expired-token");
    stubFetch(reply(401, { detail: "Not authenticated" }));

    await expect(api.listProjects()).rejects.toThrow(/session/i);
  });

  it("redirects to the login page", async () => {
    // Handled once, centrally. Without it a user whose session expired sits on
    // a screen that will not load, with nothing saying why.
    setToken("an-expired-token");
    stubFetch(reply(401, { detail: "Not authenticated" }));

    await expect(api.listProjects()).rejects.toThrow();

    expect(window.location.href).toBe("/login");
  });

  it("does not clear the token on other failures", async () => {
    // A 404 or a 500 says nothing about whether the session is valid. Clearing
    // the token there would log the user out over a missing project.
    setToken("a-good-token");
    stubFetch(reply(404, { detail: "Project not found." }));

    await expect(api.listProjects()).rejects.toThrow();

    expect(getToken()).toBe("a-good-token");
  });
});

describe("error messages", () => {
  it("unwraps FastAPI's string detail", async () => {
    stubFetch(reply(404, { detail: "Project not found." }));

    await expect(api.listProjects()).rejects.toThrow("Project not found.");
  });

  it("unwraps FastAPI's ARRAY detail from a 422", async () => {
    // The shape that is easy to miss. Validation errors arrive as a list of
    // problems, not a string -- and handling only the string case turns every
    // "which field is wrong?" into a bare status code.
    stubFetch(
      reply(422, {
        detail: [{ loc: ["body", "name"], msg: "Field required", type: "missing" }],
      })
    );

    await expect(api.listProjects()).rejects.toThrow("Field required");
  });

  it("falls back to a status message when the body is not JSON", async () => {
    // A proxy timeout or an nginx error page. Calling .json() on it throws,
    // and that throw must not escape as the user-facing error.
    stubFetch(reply(502, "<html>Bad Gateway</html>", { asText: true }));

    await expect(api.listProjects()).rejects.toThrow(/502/);
  });

  it("falls back when there is no detail field at all", async () => {
    stubFetch(reply(500, { message: "something else entirely" }));

    await expect(api.listProjects()).rejects.toThrow(/500/);
  });
});

describe("a 204 No Content response", () => {
  it("resolves instead of throwing", async () => {
    // .json() on an empty body throws, so deleting a chat session would fail
    // AFTER succeeding -- the row is gone and the UI shows an error.
    setToken("a-token");
    stubFetch(reply(204, undefined, { asText: true }));

    await expect(api.deleteChatSession("some-id")).resolves.toBeDefined();
  });
});

// ======================================================================
// getErrorMessage
// ======================================================================

describe("getErrorMessage", () => {
  it("reads the message from an Error", () => {
    expect(getErrorMessage(new Error("Something specific"))).toBe("Something specific");
  });

  it("falls back for a thrown string", () => {
    // JavaScript lets you throw anything. Assuming an Error is a real bug
    // waiting for an unusual failure.
    expect(getErrorMessage("just a string")).toBeTruthy();
  });

  it.each([null, undefined, 42, {}, []])("falls back for %s", (thrown) => {
    expect(typeof getErrorMessage(thrown)).toBe("string");
    expect(getErrorMessage(thrown).length).toBeGreaterThan(0);
  });

  it("uses the caller's fallback text", () => {
    expect(getErrorMessage(null, "Could not load projects.")).toBe(
      "Could not load projects."
    );
  });

  it("prefers a real message over the fallback", () => {
    expect(getErrorMessage(new Error("Real reason"), "Fallback")).toBe("Real reason");
  });

  it("uses the fallback for an Error with an empty message", () => {
    expect(getErrorMessage(new Error(""), "Fallback")).toBe("Fallback");
  });
});

// ======================================================================
// The two methods that deliberately bypass apiFetch
// ======================================================================

describe("login", () => {
  it("sends form encoding, not JSON", async () => {
    // OAuth2's password flow specifies this, and FastAPI's
    // OAuth2PasswordRequestForm reads exactly it. Sending JSON returns a 422.
    const mock = stubFetch(reply(200, { access_token: "t", refresh_token: "r" }));

    await api.login("ada@example.com", "a-password");

    expect(callOptions(mock).headers["Content-Type"]).toBe(
      "application/x-www-form-urlencoded"
    );
  });

  it("names the email field `username`", async () => {
    // The mismatch is in the OAuth2 spec, not in this code -- but a rename
    // here would break login with a confusing 422, so it is pinned.
    const mock = stubFetch(reply(200, { access_token: "t", refresh_token: "r" }));

    await api.login("ada@example.com", "a-password");

    const body = callOptions(mock).body as URLSearchParams;
    expect(body.get("username")).toBe("ada@example.com");
    expect(body.get("password")).toBe("a-password");
  });

  it("stores the access token on success", async () => {
    stubFetch(reply(200, { access_token: "the-new-token", refresh_token: "r" }));

    await api.login("ada@example.com", "a-password");

    expect(getToken()).toBe("the-new-token");
  });

  it("throws and stores nothing on bad credentials", async () => {
    stubFetch(reply(401, { detail: "Incorrect email or password." }));

    await expect(api.login("ada@example.com", "wrong")).rejects.toThrow();
    expect(getToken()).toBeNull();
  });
});

describe("uploadDocuments", () => {
  it("sends FormData", async () => {
    setToken("a-token");
    const mock = stubFetch(reply(200, []));

    await api.uploadDocuments("project-1", [
      new File(["content"], "handbook.pdf", { type: "application/pdf" }),
    ]);

    expect(callOptions(mock).body).toBeInstanceOf(FormData);
  });

  it("does NOT set Content-Type", async () => {
    // The browser must set it itself, because it appends a boundary parameter
    // only it knows. Setting it by hand produces a request the server cannot
    // parse -- and the failure looks like a server bug.
    setToken("a-token");
    const mock = stubFetch(reply(200, []));

    await api.uploadDocuments("project-1", [new File(["x"], "a.pdf")]);

    const headers = callOptions(mock).headers ?? {};
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("still sends the Authorization header", async () => {
    setToken("a-token");
    const mock = stubFetch(reply(200, []));

    await api.uploadDocuments("project-1", [new File(["x"], "a.pdf")]);

    expect(callOptions(mock).headers.Authorization).toBe("Bearer a-token");
  });

  it("appends every file under the same field name", async () => {
    // How one request carries several uploads. The backend reads `files` as a
    // list; a unique name per file would deliver only the last one.
    setToken("a-token");
    const mock = stubFetch(reply(200, []));

    await api.uploadDocuments("project-1", [
      new File(["a"], "first.pdf"),
      new File(["b"], "second.pdf"),
      new File(["c"], "third.pdf"),
    ]);

    const body = callOptions(mock).body as FormData;
    expect(body.getAll("files")).toHaveLength(3);
  });

  it("includes the chunking settings", async () => {
    setToken("a-token");
    const mock = stubFetch(reply(200, []));

    await api.uploadDocuments("project-1", [new File(["x"], "a.pdf")]);

    const body = callOptions(mock).body as FormData;
    expect(body.get("project_id")).toBe("project-1");
    expect(body.get("chunk_strategy")).toBeTruthy();
    expect(body.get("chunk_size")).toBeTruthy();
  });

  it("reports the backend's reason when an upload fails", async () => {
    setToken("a-token");
    stubFetch(reply(400, { detail: "This file has already been uploaded." }));

    await expect(
      api.uploadDocuments("project-1", [new File(["x"], "a.pdf")])
    ).rejects.toThrow(/already been uploaded/);
  });
});

// ======================================================================
// URLs -- the contract with the backend
// ======================================================================

describe("request URLs", () => {
  it("defaults to localhost:8000", () => {
    // Documented in docs/DEVELOPMENT.md as the default; pinned here so a change is
    // deliberate rather than accidental.
    expect(API_BASE).toContain("localhost:8000");
  });

  it.each([
    ["listProjects", () => api.listProjects(), "/api/v1/projects"],
    ["listChatSessions", () => api.listChatSessions(), "/api/v1/chat/sessions"],
    ["search", () => api.search("q"), "/api/v1/search"],
  ])("%s hits the versioned path", async (_name, call, expected) => {
    setToken("a-token");
    const mock = stubFetch(reply(200, { results: [] }));

    await call();

    expect(callUrl(mock)).toContain(expected);
  });

  it("sends the search query in the body, not the URL", async () => {
    // POST for a search is deliberate: a question can be long enough to bump
    // against query-string limits, and filters do not fit comfortably in one.
    setToken("a-token");
    const mock = stubFetch(reply(200, { results: [] }));

    await api.search("how much annual leave do I get?");

    expect(callOptions(mock).method).toBe("POST");
    expect(callUrl(mock)).not.toContain("annual%20leave");
    expect(JSON.parse(callOptions(mock).body as string).query).toBe(
      "how much annual leave do I get?"
    );
  });
});

// ======================================================================
// askQuestion -- the chat request and its answer mode
// ======================================================================

describe("askQuestion", () => {
  it("posts to the session's messages path", async () => {
    setToken("a-token");
    const mock = stubFetch(reply(200, {}));

    await api.askQuestion("session-1", "a question");

    expect(callOptions(mock).method).toBe("POST");
    expect(callUrl(mock)).toContain("/api/v1/chat/sessions/session-1/messages");
  });

  it("sends only the message when no mode is given", async () => {
    // Exactly the body the backend has always accepted, which it treats as
    // "rag". A caller that knows nothing about modes changes nothing.
    setToken("a-token");
    const mock = stubFetch(reply(200, {}));

    await api.askQuestion("session-1", "a question");

    expect(JSON.parse(callOptions(mock).body as string)).toEqual({
      message: "a question",
    });
  });

  it.each(["rag", "agent"] as const)("includes mode %s when given", async (mode) => {
    setToken("a-token");
    const mock = stubFetch(reply(200, {}));

    await api.askQuestion("session-1", "a question", mode);

    expect(JSON.parse(callOptions(mock).body as string)).toEqual({
      message: "a question",
      mode,
    });
  });

  it("returns the agent's steps from the response", async () => {
    setToken("a-token");
    const steps = [
      { tool: "search_documents", args: { query: "q" }, summary: "Found 3 excerpts" },
    ];
    stubFetch(reply(200, { mode: "agent", steps, citations: [] }));

    const response = await api.askQuestion("session-1", "a question", "agent");

    expect(response.mode).toBe("agent");
    expect(response.steps).toEqual(steps);
  });
});

// ======================================================================
// Sessions: silent refresh and server-side logout
// ======================================================================

describe("silent refresh on a 401", () => {
  it("trades the refresh token for a new pair and retries once", async () => {
    setToken("expired-access");
    setRefreshToken("the-refresh");
    const mock = stubFetch(
      reply(401, { detail: "expired" }),
      reply(200, { access_token: "new-access", refresh_token: "new-refresh" }),
      reply(200, [{ id: "p1" }])
    );

    const projects = await api.listProjects();

    expect(projects).toEqual([{ id: "p1" }]);
    expect(callUrl(mock, 1)).toContain("/auth/refresh");
    expect(JSON.parse(callOptions(mock, 1).body)).toEqual({ refresh_token: "the-refresh" });
    expect(callOptions(mock, 2).headers.Authorization).toBe("Bearer new-access");
    expect(getToken()).toBe("new-access");
    expect(getRefreshToken()).toBe("new-refresh");
  });

  it("signs out when the refresh is refused", async () => {
    setToken("expired-access");
    setRefreshToken("revoked-refresh");
    stubFetch(reply(401, { detail: "expired" }), reply(401, { detail: "revoked" }));

    await expect(api.listProjects()).rejects.toThrow(/session/i);
    expect(getToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(window.location.href).toBe("/login");
  });

  it("does not loop when the retried request is also a 401", async () => {
    setToken("expired-access");
    setRefreshToken("the-refresh");
    const mock = stubFetch(
      reply(401, {}),
      reply(200, { access_token: "a", refresh_token: "r" }),
      reply(401, {})
    );

    await expect(api.listProjects()).rejects.toThrow(/session/i);
    expect(mock).toHaveBeenCalledTimes(3);
  });

  it("shares ONE refresh between simultaneous 401s", async () => {
    // The backend treats a refresh token presented twice as stolen and ends
    // the session, so two parallel refreshes would sign the user out.
    setToken("expired-access");
    setRefreshToken("the-refresh");
    const mock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/auth/refresh")) {
        return reply(200, { access_token: "new-access", refresh_token: "new-refresh" });
      }
      const headers = (init?.headers ?? {}) as Record<string, string>;
      return headers.Authorization === "Bearer new-access" ? reply(200, []) : reply(401, {});
    });
    vi.stubGlobal("fetch", mock);

    await Promise.all([api.listProjects(), api.listChatSessions()]);

    const refreshes = mock.mock.calls.filter(([url]) => String(url).includes("/auth/refresh"));
    expect(refreshes).toHaveLength(1);
  });
});

describe("login and logout tokens", () => {
  it("login stores the refresh token too", async () => {
    stubFetch(reply(200, { access_token: "a", refresh_token: "the-refresh" }));

    await api.login("ada@example.com", "a-password");

    expect(getRefreshToken()).toBe("the-refresh");
  });

  it("logout clears both tokens and asks the backend to revoke them", async () => {
    setToken("the-access");
    setRefreshToken("the-refresh");
    const mock = stubFetch(reply(204));

    api.logout();

    expect(getToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(callUrl(mock)).toContain("/auth/logout");
    expect(callOptions(mock).headers.Authorization).toBe("Bearer the-access");
    expect(JSON.parse(callOptions(mock).body)).toEqual({ refresh_token: "the-refresh" });
  });

  it("logout with nothing stored makes no request", () => {
    const mock = stubFetch();
    api.logout();
    expect(mock).not.toHaveBeenCalled();
  });
});

describe("account and admin endpoints", () => {
  it("signup with an invite sends the token instead of an org name", async () => {
    const mock = stubFetch(reply(201, { id: "u", email: "a@b.com", role: "Employee" }));

    await api.signup("a@b.com", "a-long-password", "Ignored Org", "invite-token");

    const body = JSON.parse(callOptions(mock).body);
    expect(body).toEqual({ email: "a@b.com", password: "a-long-password", invite_token: "invite-token" });
  });

  it("signup without an invite sends the org name", async () => {
    const mock = stubFetch(reply(201, { id: "u", email: "a@b.com", role: "Admin" }));

    await api.signup("a@b.com", "a-long-password", "Acme");

    expect(JSON.parse(callOptions(mock).body).org_name).toBe("Acme");
  });

  it("builds the audit log query from only the filters given", async () => {
    setToken("t");
    const mock = stubFetch(reply(200, { items: [], total: 0, limit: 20, offset: 40 }));

    await api.listAuditLogs({ limit: 20, offset: 40, action: "member.", user_id: undefined });

    expect(callUrl(mock)).toMatch(/\/org\/audit-logs\?limit=20&offset=40&action=member\.$/);
  });

  it("asks for a viewer API key by default", async () => {
    setToken("t");
    const mock = stubFetch(reply(201, { key: "dm_x" }));

    await api.createApiKey("CI");

    expect(JSON.parse(callOptions(mock).body)).toEqual({
      name: "CI",
      role: "viewer",
      expires_in_days: null,
    });
  });

  it("sends password reset confirmation with the backend's field names", async () => {
    const mock = stubFetch(reply(200, { detail: "ok" }));

    await api.confirmPasswordReset("tok", "a-new-password");

    expect(callUrl(mock)).toContain("/auth/password-reset/confirm");
    expect(JSON.parse(callOptions(mock).body)).toEqual({ token: "tok", new_password: "a-new-password" });
  });
});
