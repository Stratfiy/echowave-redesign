import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GoogleSignInButton } from "./GoogleSignInButton";
import { PasswordRecoveryForm } from "./PasswordRecoveryForm";
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: { backendApiEndpoint: "https://api.example.com" } }) }));
vi.mock("@/components/auth/AuthShell", () => ({ AuthShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
beforeEach(() => { vi.stubGlobal("fetch", vi.fn()); window.history.replaceState({}, "", "/auth/forgot-password"); });
afterEach(() => vi.unstubAllGlobals());
function reply(body: unknown, status = 200) { vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(body), { status })); }
function fillReset(password = "new-password", confirmation = password) {
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: confirmation } });
  fireEvent.submit(screen.getByRole("form", { name: "Password recovery" }));
}
describe("password recovery", () => {
  it("shows a generic response without claiming an account exists", async () => {
    reply({ message: "generic" }); render(<PasswordRecoveryForm />);
    fireEvent.change(screen.getByLabelText("Account email"), { target: { value: "someone@example.com" } });
    fireEvent.submit(screen.getByRole("form", { name: "Password recovery" }));
    expect((await screen.findByRole("status")).textContent).toContain("If an account exists for this address");
    expect(fetch).toHaveBeenCalledWith("https://api.example.com/api/v1/auth/password-reset/request", expect.objectContaining({ body: JSON.stringify({ email: "someone@example.com" }) }));
  });
  it("requires a token and never submits an incomplete reset link", () => {
    render(<PasswordRecoveryForm reset />);
    expect(screen.getByRole("alert").textContent).toContain("incomplete");
    expect(screen.queryByRole("button", { name: "Update password" })).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("removes the token fragment, submits it in the body and returns to sign-in", async () => {
    window.history.replaceState({}, "", "/auth/reset-password#token=" + "a".repeat(43));
    reply({ message: "updated" }); render(<StrictMode><PasswordRecoveryForm reset /></StrictMode>);
    expect(window.location.hash).toBe("");
    fillReset();
    expect((await screen.findByRole("status")).textContent).toContain("two-factor authentication stays enabled");
    expect(fetch).toHaveBeenCalledWith("https://api.example.com/api/v1/auth/password-reset/confirm", expect.objectContaining({ body: JSON.stringify({ token: "a".repeat(43), password: "new-password" }) }));
  });
  it("rejects mismatched passwords before submitting", () => {
    window.history.replaceState({}, "", "/auth/reset-password#token=" + "a".repeat(43));
    render(<PasswordRecoveryForm reset />); fillReset("new-password", "different-password");
    expect(screen.getByRole("alert").textContent).toContain("do not match");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("shows an expired-link error with a path to request another", async () => {
    window.history.replaceState({}, "", "/auth/reset-password#token=" + "a".repeat(43));
    reply({ detail: "This reset link is invalid or expired." }, 400);
    render(<PasswordRecoveryForm reset />); fillReset();
    expect((await screen.findByRole("alert")).textContent).toContain("expired");
    expect(screen.getByRole("link", { name: "Request a new reset link" })).toBeTruthy();
  });
  it("recovers from network failures", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("offline")); render(<PasswordRecoveryForm />);
    fireEvent.change(screen.getByLabelText("Account email"), { target: { value: "someone@example.com" } });
    fireEvent.submit(screen.getByRole("form", { name: "Password recovery" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Check your connection");
    expect(screen.getByRole("button", { name: "Send reset link" }).hasAttribute("disabled")).toBe(false);
  });
});
describe("Google sign-in", () => {
  it("uses a top-level start link so the state cookie is set without CORS credentials", async () => {
    reply({ enabled: true }); render(<GoogleSignInButton referralCode="partner one" />);
    const link = await screen.findByRole("link", { name: "Continue with Google" });
    expect(link.getAttribute("href")).toBe("https://api.example.com/api/v1/auth/google/start?redirect=true&ref=partner+one");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith("https://api.example.com/api/v1/auth/google/status");
  });
  it("does not offer an unconfigured provider", async () => {
    reply({ enabled: false }); render(<GoogleSignInButton />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("link", { name: "Continue with Google" })).toBeNull();
  });
  it("allows retrying a failed readiness check", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("offline")); render(<GoogleSignInButton />);
    expect((await screen.findByRole("status")).textContent).toContain("could not be checked");
    reply({ enabled: true }); fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: "Continue with Google" })).toBeTruthy();
  });
});
