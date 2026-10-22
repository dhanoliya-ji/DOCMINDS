/**
 * policy.test.ts
 * ==============
 * WHAT THIS FILE TESTS
 * --------------------
 * The three small rule modules the screens lean on:
 *
 *   lib/password.ts        the password policy, which must agree with the
 *                          backend's `password_problems` word for word, or
 *                          the form will pass a password the API refuses
 *   lib/roles.ts           who may do what, for both role spellings
 *   security-headers.mjs   the CSP and friends served on every page
 *
 * Each is pure logic, so these tests need no DOM and no mocks.
 */

import { describe, expect, it } from "vitest";

import { PASSWORD_MIN_LENGTH, passwordProblems } from "@/lib/password";
import { canEdit, isAdmin, roleAlias, roleLabel } from "@/lib/roles";
import { securityHeaders } from "@/security-headers.mjs";

describe("password policy (mirrors backend password_problems)", () => {
  it("defaults to the backend's minimum of 10", () => {
    expect(PASSWORD_MIN_LENGTH).toBe(10);
  });

  it("accepts a long passphrase", () => {
    expect(passwordProblems("correct horse battery", "ada@example.com")).toEqual([]);
  });

  it("refuses a short password in the backend's words", () => {
    expect(passwordProblems("short")).toEqual(["Password must be at least 10 characters long."]);
  });

  it("refuses digits only, however long", () => {
    expect(passwordProblems("12345678901")).toEqual(["Password cannot be made of numbers only."]);
  });

  it("refuses the email address or its local part, ignoring case", () => {
    expect(passwordProblems("ADA.LOVELACE@example.com", "ada.lovelace@example.com")).toContain(
      "Password cannot be the same as your email address."
    );
    expect(passwordProblems("ada.lovelace", "ada.lovelace@example.com")).toContain(
      "Password cannot be the same as your email address."
    );
  });

  it("lists every problem at once", () => {
    expect(passwordProblems("123")).toHaveLength(2);
  });
});

describe("roles", () => {
  it.each([
    ["Admin", true, true, "Admin"],
    ["admin", true, true, "Admin"],
    ["Manager", true, false, "Editor"],
    ["editor", true, false, "Editor"],
    ["Employee", false, false, "Viewer"],
    ["viewer", false, false, "Viewer"],
  ])("%s: canEdit=%s isAdmin=%s label=%s", (role, edit, admin, label) => {
    expect(canEdit(role)).toBe(edit);
    expect(isAdmin(role)).toBe(admin);
    expect(roleLabel(role)).toBe(label);
  });

  it("fails closed for an unknown or missing role", () => {
    for (const role of [undefined, null, "", "superuser"]) {
      expect(canEdit(role)).toBe(false);
      expect(isAdmin(role)).toBe(false);
    }
  });

  it("maps stored names to the aliases the selects use", () => {
    expect(roleAlias("Admin")).toBe("admin");
    expect(roleAlias("Manager")).toBe("editor");
    expect(roleAlias("Employee")).toBe("viewer");
  });
});

describe("security headers", () => {
  function header(headers: { key: string; value: string }[], key: string) {
    return headers.find((h) => h.key === key)?.value;
  }

  it("allows the API origin, and only its origin, in connect-src", () => {
    const csp = header(securityHeaders({ apiUrl: "https://api.example.com/some/path" }), "Content-Security-Policy")!;
    expect(csp).toMatch(/connect-src 'self' https:\/\/api\.example\.com(;|$)/);
    expect(csp).not.toContain("/some/path");
  });

  it("defaults the API origin to the local backend", () => {
    const csp = header(securityHeaders({}), "Content-Security-Policy")!;
    expect(csp).toContain("connect-src 'self' http://localhost:8000");
  });

  it("forbids framing, plugins and foreign form posts", () => {
    const headers = securityHeaders({ apiUrl: "https://api.example.com" });
    const csp = header(headers, "Content-Security-Policy")!;
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("form-action 'self'");
    expect(csp).toContain("base-uri 'self'");
    expect(header(headers, "X-Frame-Options")).toBe("DENY");
    expect(header(headers, "Referrer-Policy")).toBe("strict-origin-when-cross-origin");
    expect(header(headers, "Permissions-Policy")).toContain("camera=()");
    expect(header(headers, "X-Content-Type-Options")).toBe("nosniff");
  });

  it("allows eval and websockets only in development (React Refresh)", () => {
    const prod = header(securityHeaders({ isDev: false }), "Content-Security-Policy")!;
    const dev = header(securityHeaders({ isDev: true }), "Content-Security-Policy")!;
    expect(prod).not.toContain("unsafe-eval");
    expect(prod).not.toContain("ws:");
    expect(dev).toContain("'unsafe-eval'");
    expect(dev).toContain("ws:");
  });

  it("upgrades insecure requests only when the API is HTTPS", () => {
    // Upgrading http://localhost:8000 would break local runs and the e2e job.
    const local = header(securityHeaders({ apiUrl: "http://localhost:8000" }), "Content-Security-Policy")!;
    const live = header(securityHeaders({ apiUrl: "https://api.example.com" }), "Content-Security-Policy")!;
    expect(local).not.toContain("upgrade-insecure-requests");
    expect(live).toContain("upgrade-insecure-requests");
  });

  it("sends HSTS in production builds only", () => {
    expect(header(securityHeaders({ isDev: false }), "Strict-Transport-Security")).toMatch(/^max-age=/);
    expect(header(securityHeaders({ isDev: true }), "Strict-Transport-Security")).toBeUndefined();
  });
});
