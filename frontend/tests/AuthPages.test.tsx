/**
 * AuthPages.test.tsx
 * ==================
 * WHAT THIS FILE TESTS
 * --------------------
 * The signed-out screens: /login (sign in, sign up, accept an invite),
 * /forgot-password, /reset-password and /verify-email.
 *
 * THE CONTRACT WORTH PINNING
 * --------------------------
 * The backend's emails link to `/login?invite=`, `/reset-password?token=` and
 * `/verify-email?token=` (backend/app/services/email.py). These tests drive
 * each page through exactly those query parameters, so renaming one side
 * without the other fails here instead of in someone's inbox.
 *
 * `lib/api` is partly mocked: the `api` calls are stubs, but ApiError and
 * getErrorMessage are the REAL ones, because how an error is worded is part of
 * what these pages do.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ForgotPasswordPage from "@/app/forgot-password/page";
import LoginPage from "@/app/login/page";
import ResetPasswordPage from "@/app/reset-password/page";
import VerifyEmailPage from "@/app/verify-email/page";
import { ApiError } from "@/lib/api";
import { pushMock, searchParamsMock } from "./setup";

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  signup: vi.fn(),
  previewInvite: vi.fn(),
  requestPasswordReset: vi.fn(),
  confirmPasswordReset: vi.fn(),
  verifyEmail: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...mocks } };
});

beforeEach(() => {
  Object.values(mocks).forEach((m) => m.mockReset());
  mocks.login.mockResolvedValue({ access_token: "a", refresh_token: "r" });
  mocks.signup.mockResolvedValue({ id: "u", email: "ada@example.com", role: "Admin" });
});

const GOOD_PASSWORD = "a long passphrase";

// ======================================================================
// /login
// ======================================================================

describe("sign in", () => {
  it("signs in and goes to the dashboard", async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByPlaceholderText("you@company.com"), "ada@example.com");
    await user.type(screen.getByPlaceholderText("Password"), "whatever-it-is");
    await user.click(screen.getByRole("button", { name: /^Sign in/ }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(mocks.login).toHaveBeenCalledWith("ada@example.com", "whatever-it-is");
  });

  it("shows the backend's reason, e.g. a rate limit", async () => {
    mocks.login.mockRejectedValue(
      new ApiError("Too many sign-in attempts. Please try again in 40 seconds.", 429, 40)
    );
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByPlaceholderText("you@company.com"), "ada@example.com");
    await user.type(screen.getByPlaceholderText("Password"), "x");
    await user.click(screen.getByRole("button", { name: /^Sign in/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/try again in 40 seconds/);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("links to the forgot-password page", () => {
    render(<LoginPage />);
    expect(screen.getByRole("link", { name: /forgot password/i })).toHaveAttribute("href", "/forgot-password");
  });
});

describe("sign up (new organization)", () => {
  async function fillSignup(password = GOOD_PASSWORD) {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.click(screen.getByRole("button", { name: "Create one" }));
    await user.type(screen.getByPlaceholderText("Organization name"), "Acme");
    await user.type(screen.getByPlaceholderText("you@company.com"), "ada@example.com");
    await user.type(screen.getByPlaceholderText("Password"), password);
    return user;
  }

  it("asks for an organization name and creates the org, then signs in", async () => {
    const user = await fillSignup();
    await user.click(screen.getByRole("button", { name: /Create workspace/ }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(mocks.signup).toHaveBeenCalledWith("ada@example.com", GOOD_PASSWORD, "Acme", undefined);
    expect(mocks.login).toHaveBeenCalled();
  });

  it("shows the password rules as you type, and blocks a weak password", async () => {
    const user = await fillSignup("short");

    expect(screen.getByText("Password must be at least 10 characters long.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Create workspace/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/at least 10 characters/);
    expect(mocks.signup).not.toHaveBeenCalled();
  });

  it("links the hints to the password field for screen readers", async () => {
    await fillSignup("short");
    const hintsId = screen.getByPlaceholderText("Password").getAttribute("aria-describedby");
    expect(hintsId).toBeTruthy();
    expect(document.getElementById(hintsId!)).toHaveTextContent(/at least 10/);
  });

  it("treats 'confirm your email first' after signup as success", async () => {
    // REQUIRE_EMAIL_VERIFICATION on: the account exists but cannot sign in yet.
    mocks.login.mockRejectedValue(new ApiError("Please confirm your email address first.", 403));
    const user = await fillSignup();
    await user.click(screen.getByRole("button", { name: /Create workspace/ }));

    expect(await screen.findByRole("status")).toHaveTextContent(/emailed you a link/);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
    // Back on the sign-in form.
    expect(screen.getByRole("button", { name: /^Sign in/ })).toBeInTheDocument();
  });
});

describe("accepting an invite (/login?invite=...)", () => {
  beforeEach(() => {
    searchParamsMock.value = new URLSearchParams("invite=inv-token");
  });

  it("previews the invite and joins without an organization name", async () => {
    mocks.previewInvite.mockResolvedValue({
      email: "bob@example.com",
      role: "Manager",
      org_name: "Acme",
      expires_at: "2030-01-01T00:00:00Z",
    });
    const user = userEvent.setup();
    render(<LoginPage />);

    expect(await screen.findByRole("heading", { name: "Join Acme" })).toBeInTheDocument();
    expect(screen.getByText(/invited as Editor/)).toBeInTheDocument();
    expect(mocks.previewInvite).toHaveBeenCalledWith("inv-token");
    expect(screen.queryByPlaceholderText("Organization name")).not.toBeInTheDocument();

    // The invite is bound to one address: filled in and locked.
    const email = screen.getByPlaceholderText("you@company.com");
    expect(email).toHaveValue("bob@example.com");
    expect(email).toHaveAttribute("readonly");

    await user.type(screen.getByPlaceholderText("Password"), GOOD_PASSWORD);
    await user.click(screen.getByRole("button", { name: /Join workspace/ }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(mocks.signup).toHaveBeenCalledWith("bob@example.com", GOOD_PASSWORD, "", "inv-token");
  });

  it("explains a dead invite and falls back to creating a workspace", async () => {
    mocks.previewInvite.mockRejectedValue(new ApiError("This invite is invalid or has expired.", 404));
    render(<LoginPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid or has expired.*Ask your admin/);
    expect(screen.getByPlaceholderText("Organization name")).toBeInTheDocument();
  });
});

// ======================================================================
// /forgot-password
// ======================================================================

describe("forgot password", () => {
  it("sends the request and shows the same answer whatever the address", async () => {
    mocks.requestPasswordReset.mockResolvedValue({
      detail: "If an account exists for that email, a reset link has been sent.",
    });
    const user = userEvent.setup();
    render(<ForgotPasswordPage />);

    await user.type(screen.getByPlaceholderText("you@company.com"), "nobody@example.com");
    await user.click(screen.getByRole("button", { name: /Send reset link/ }));

    expect(await screen.findByRole("status")).toHaveTextContent(/If an account exists/);
    expect(mocks.requestPasswordReset).toHaveBeenCalledWith("nobody@example.com");
  });
});

// ======================================================================
// /reset-password?token=...
// ======================================================================

describe("reset password", () => {
  it("says the link is broken when it has no token", () => {
    render(<ResetPasswordPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(/missing its reset token/);
  });

  it("sets the new password with the token from the link", async () => {
    searchParamsMock.value = new URLSearchParams("token=reset-tok");
    mocks.confirmPasswordReset.mockResolvedValue({ detail: "Your password has been changed." });
    const user = userEvent.setup();
    render(<ResetPasswordPage />);

    await user.type(screen.getByPlaceholderText("New password"), GOOD_PASSWORD);
    await user.type(screen.getByPlaceholderText("Confirm new password"), GOOD_PASSWORD);
    await user.click(screen.getByRole("button", { name: /Set new password/ }));

    expect(await screen.findByRole("status")).toHaveTextContent(/has been changed/);
    expect(mocks.confirmPasswordReset).toHaveBeenCalledWith("reset-tok", GOOD_PASSWORD);
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("refuses mismatched passwords before sending anything", async () => {
    searchParamsMock.value = new URLSearchParams("token=reset-tok");
    const user = userEvent.setup();
    render(<ResetPasswordPage />);

    await user.type(screen.getByPlaceholderText("New password"), GOOD_PASSWORD);
    await user.type(screen.getByPlaceholderText("Confirm new password"), GOOD_PASSWORD + "x");
    await user.click(screen.getByRole("button", { name: /Set new password/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/do not match/);
    expect(mocks.confirmPasswordReset).not.toHaveBeenCalled();
  });

  it("offers a new link when the token has expired", async () => {
    searchParamsMock.value = new URLSearchParams("token=old");
    mocks.confirmPasswordReset.mockRejectedValue(
      new ApiError("This reset link is invalid or has expired. Please request a new one.", 400)
    );
    render(<ResetPasswordPage />);

    fireEvent.change(screen.getByPlaceholderText("New password"), { target: { value: GOOD_PASSWORD } });
    fireEvent.change(screen.getByPlaceholderText("Confirm new password"), { target: { value: GOOD_PASSWORD } });
    fireEvent.click(screen.getByRole("button", { name: /Set new password/ }));

    expect(await screen.findByRole("link", { name: /Request a new link/ })).toHaveAttribute(
      "href",
      "/forgot-password"
    );
  });
});

// ======================================================================
// /verify-email?token=...
// ======================================================================

describe("verify email", () => {
  it("confirms the address as soon as the page opens", async () => {
    searchParamsMock.value = new URLSearchParams("token=verify-ok");
    mocks.verifyEmail.mockResolvedValue({ detail: "Your email address has been confirmed." });
    render(<VerifyEmailPage />);

    expect(await screen.findByText("Your email address has been confirmed.")).toBeInTheDocument();
    expect(mocks.verifyEmail).toHaveBeenCalledWith("verify-ok");
  });

  it("sends a single-use token once, even if the page renders twice", async () => {
    // Strict Mode runs effects twice in development; a second POST would fail
    // and show an error for a verification that succeeded.
    searchParamsMock.value = new URLSearchParams("token=verify-once");
    mocks.verifyEmail.mockResolvedValue({ detail: "Confirmed." });
    const first = render(<VerifyEmailPage />);
    first.unmount();
    render(<VerifyEmailPage />);

    expect(await screen.findByText("Confirmed.")).toBeInTheDocument();
    expect(mocks.verifyEmail).toHaveBeenCalledTimes(1);
  });

  it("explains an expired link", async () => {
    searchParamsMock.value = new URLSearchParams("token=verify-bad");
    mocks.verifyEmail.mockRejectedValue(
      new ApiError("This verification link is invalid or has expired. Please request a new one.", 400)
    );
    render(<VerifyEmailPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid or has expired/);
  });
});
