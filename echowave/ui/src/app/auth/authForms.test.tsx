import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "./login/LoginForm";
import SignupPage from "./signup/page";

const mocks = vi.hoisted(() => ({ login: vi.fn(), signup: vi.fn(), error: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
  loginApiV1AuthLoginPost: mocks.login,
  signupApiV1AuthSignupPost: mocks.signup,
}));
vi.mock("sonner", () => ({ toast: { error: mocks.error } }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("@/components/auth/AuthShell", () => ({
  AuthShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/auth/AuthEnterpriseCTA", () => ({ AuthEnterpriseCTA: () => null }));
vi.mock("@/components/auth/GoogleSignInButton", () => ({ GoogleSignInButton: () => null }));

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => vi.unstubAllGlobals());

function submit(kind: "login" | "signup") {
  render(kind === "login" ? <LoginForm signupEnabled /> : <SignupPage />);
  fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "audit@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "test-password-123" } });
  if (kind === "signup") {
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "test-password-123" } });
  }
  fireEvent.submit(screen.getByTestId(`${kind}-form`));
}

describe.each(["login", "signup"] as const)("%s failure handling", (kind) => {
  it("shows field validation errors as text, without starting a session", async () => {
    mocks[kind].mockResolvedValue({
      error: { detail: [{ type: "value_error", loc: ["body", "email"], msg: "Invalid email address" }] },
    });
    submit(kind);
    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith("email: Invalid email address"));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reports a rejected cookie session instead of treating authentication as complete", async () => {
    mocks[kind].mockResolvedValue({ data: { token: "test-token", user: { id: 1 } } });
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 502 }));
    submit(kind);
    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith(kind === "login"
      ? "Could not save your sign-in session. Please try again."
      : "Your account was created, but we could not save your session. Please sign in."));
    expect(screen.getByTestId(`${kind}-submit-btn`).hasAttribute("disabled")).toBe(false);
  });
});

it("still requests the second factor when the password was accepted", async () => {
  mocks.login.mockResolvedValue({ error: { detail: "mfa_required" } });
  submit("login");
  await waitFor(() => expect(screen.getByLabelText("Authentication code")).toBeTruthy());
  expect(mocks.error).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});
