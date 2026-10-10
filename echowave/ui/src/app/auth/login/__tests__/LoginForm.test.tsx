import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "../LoginForm";

const loginMock = vi.fn();
const joinMock = vi.fn();

vi.mock("@/client/sdk.gen", () => ({
  loginApiV1AuthLoginPost: (...args: unknown[]) => loginMock(...args),
  joinWaitlistApiV1PublicEarlyAccessWaitlistPost: (...args: unknown[]) => joinMock(...args),
}));
vi.mock("@/context/AppConfigContext", () => ({
  useAppConfig: () => ({ config: { backendApiEndpoint: "" } }),
}));

const stepOf = () => screen.getByTestId("login-flow").querySelector("[data-step]")?.getAttribute("data-step");

function toPassword() {
  fireEvent.change(screen.getByTestId("login-email-input"), { target: { value: "asha@example.com" } });
  fireEvent.submit(screen.getByTestId("login-form"));
}

describe("log-in in the same shell", () => {
  beforeEach(() => {
    loginMock.mockReset();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
  });

  it("asks for the email, then the password, with Forgot password beside it", () => {
    render(<LoginForm signupEnabled />);
    expect(stepOf()).toBe("email");
    expect(screen.getByTestId("login-signup-link")).toBeTruthy();
    toPassword();
    expect(stepOf()).toBe("password");
    expect(screen.getByTestId("login-forgot-link").getAttribute("href")).toBe("/auth/forgot");
    fireEvent.click(screen.getByTestId("auth-back"));
    expect(stepOf()).toBe("email");
  });

  it("keeps a wrong password on the password step", async () => {
    loginMock.mockResolvedValue({ error: { detail: "Invalid email or password" }, response: { status: 401 } });
    render(<LoginForm signupEnabled={false} />);
    expect(screen.queryByTestId("login-signup-link")).toBeNull();
    toPassword();
    fireEvent.change(screen.getByTestId("login-password-input"), { target: { value: "nope-nope" } });
    fireEvent.click(screen.getByTestId("login-submit-btn"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Invalid email or password"));
    expect(stepOf()).toBe("password");
  });

  it("asks for the second factor only once the server does", async () => {
    loginMock
      .mockResolvedValueOnce({ error: { detail: "mfa_required" }, response: { status: 401 } })
      .mockResolvedValueOnce({ error: { detail: "stop" }, response: { status: 401 } });
    render(<LoginForm signupEnabled />);
    toPassword();
    fireEvent.change(screen.getByTestId("login-password-input"), { target: { value: "correct-horse" } });
    fireEvent.click(screen.getByTestId("login-submit-btn"));
    await waitFor(() => expect(stepOf()).toBe("mfa"));
    fireEvent.change(screen.getByTestId("login-mfa-input"), { target: { value: "123456" } });
    fireEvent.click(screen.getByTestId("login-submit-btn"));
    await waitFor(() => expect(loginMock).toHaveBeenCalledTimes(2));
    expect(loginMock.mock.calls[1][0].body.mfa_code).toBe("123456");
  });

  it("reveals the password on request", () => {
    render(<LoginForm signupEnabled />);
    toPassword();
    const input = screen.getByTestId("login-password-input");
    expect(input.getAttribute("type")).toBe("password");
    fireEvent.click(screen.getByTestId("auth-password-toggle"));
    expect(input.getAttribute("type")).toBe("text");
  });

  it("offers to ask for a code when Google sign-in was refused for want of one", async () => {
    joinMock.mockResolvedValue({ data: { state: "waitlisted", created: true }, error: undefined });
    window.history.replaceState(
      {},
      "",
      "/auth/login?error=" + encodeURIComponent("Decibyl is invite-only for now. Enter your invite code to create an account."),
    );
    render(<LoginForm signupEnabled />);
    await waitFor(() => expect(screen.getByTestId("ask-code-open")).toBeTruthy());
    fireEvent.change(screen.getByTestId("login-email-input"), { target: { value: "asha@example.com" } });
    fireEvent.click(screen.getByTestId("ask-code-open"));
    expect((screen.getByTestId("ask-code-email") as HTMLInputElement).value).toBe("asha@example.com");
    fireEvent.click(screen.getByTestId("ask-code-submit"));
    await waitFor(() => expect(screen.getByTestId("ask-code-result").textContent).toMatch(/as soon as it's approved/));
    expect(joinMock.mock.calls[0][0].body.email).toBe("asha@example.com");
  });

  it("does not offer the ask on an ordinary sign-in", () => {
    window.history.replaceState({}, "", "/auth/login");
    render(<LoginForm signupEnabled />);
    expect(screen.queryByTestId("ask-code-open")).toBeNull();
  });
});
