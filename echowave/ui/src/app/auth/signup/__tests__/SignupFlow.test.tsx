import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SignupFlow } from "../SignupFlow";

const signupMock = vi.fn();
let inviteOnly = false;
let search = new URLSearchParams();

vi.mock("@/client/sdk.gen", () => ({
  signupApiV1AuthSignupPost: (...args: unknown[]) => signupMock(...args),
}));
vi.mock("@/lib/features", () => ({
  useFeature: (name: string) => (name === "invite_only_signup" ? inviteOnly : false),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => search,
}));
vi.mock("posthog-js", () => ({ default: { capture: vi.fn() } }));
vi.mock("@/context/AppConfigContext", () => ({
  useAppConfig: () => ({ config: { backendApiEndpoint: "" } }),
}));

/** Google configured unless a test says otherwise. */
function stubFetch(googleAvailable = true) {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (String(url).includes("/auth/google/start?")) {
      return Promise.resolve({ ok: true, json: async () => ({ detail: "stop here" }) });
    }
    if (String(url).includes("/auth/google/start")) {
      return Promise.resolve({ ok: googleAvailable });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const next = () => fireEvent.click(screen.getByTestId("signup-next"));
const stepOf = () => screen.getByTestId("signup-flow").querySelector("[data-step]")?.getAttribute("data-step");

function fillEmail(value = "asha@example.com") {
  fireEvent.change(screen.getByTestId("signup-email-input"), { target: { value } });
  next();
}
function fillInvite(value = "ABCD-2345") {
  fireEvent.change(screen.getByTestId("signup-invite-input"), { target: { value } });
  next();
}
function fillName(value = "Asha Rao") {
  fireEvent.change(screen.getByTestId("signup-name-input"), { target: { value } });
  next();
}
function fillPassword(value = "correct-horse") {
  fireEvent.change(screen.getByTestId("signup-password-input"), { target: { value } });
  next();
}

describe("sign-up, one question per screen", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    signupMock.mockReset();
    inviteOnly = false;
    search = new URLSearchParams();
    stubFetch();
    // jsdom has no ResizeObserver; Radix's checkbox measures itself with one.
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  });

  it("advances on Enter and goes back with Back", () => {
    render(<SignupFlow />);
    expect(stepOf()).toBe("start");
    expect(screen.queryByTestId("auth-back")).toBeNull();

    fireEvent.change(screen.getByTestId("signup-email-input"), { target: { value: "asha@example.com" } });
    // Enter in a single-field form is an implicit submit.
    fireEvent.submit(screen.getByTestId("signup-form"));
    expect(stepOf()).toBe("name");
    expect(document.activeElement).toBe(screen.getByTestId("signup-name-input"));

    fillName();
    expect(stepOf()).toBe("password");
    expect(screen.getByTestId("auth-progress").getAttribute("aria-valuenow")).toBe("3");
    expect(screen.getByTestId("auth-progress").getAttribute("aria-valuemax")).toBe("4");

    fireEvent.click(screen.getByTestId("auth-back"));
    expect(stepOf()).toBe("name");
    // The answer survives going back.
    expect((screen.getByTestId("signup-name-input") as HTMLInputElement).value).toBe("Asha Rao");
    fireEvent.click(screen.getByTestId("auth-back"));
    expect(stepOf()).toBe("start");
  });

  it("does not advance past an invalid answer, and says why", () => {
    render(<SignupFlow />);
    fillEmail("not-an-email");
    expect(stepOf()).toBe("start");
    expect(screen.getByRole("alert").textContent).toMatch(/valid email/i);
    expect(screen.getByTestId("signup-email-input").getAttribute("aria-invalid")).toBe("true");
  });

  it("skips the invite step while invite-only is off", () => {
    render(<SignupFlow />);
    fillEmail();
    expect(stepOf()).toBe("name");
    expect(screen.queryByTestId("signup-invite-input")).toBeNull();
  });

  it("asks for the invite code when invite-only is on, prefilled from the link", () => {
    inviteOnly = true;
    search = new URLSearchParams("code=WXYZ-9876");
    render(<SignupFlow />);
    fillEmail();
    expect(stepOf()).toBe("invite");
    expect((screen.getByTestId("signup-invite-input") as HTMLInputElement).value).toBe("WXYZ-9876");
    next();
    expect(stepOf()).toBe("name");
  });

  it("requires the agreement before creating the account, then sends it", async () => {
    signupMock.mockResolvedValue({
      data: { token: "t", user: {}, email_verification_required: true },
      error: undefined,
      response: { status: 200 },
    });
    render(<SignupFlow />);
    fillEmail();
    fillName();
    fillPassword();
    expect(stepOf()).toBe("agreement");

    fireEvent.click(screen.getByTestId("signup-submit-btn"));
    expect(screen.getByRole("alert").textContent).toMatch(/agree/i);
    expect(signupMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("signup-agree-checkbox"));
    fireEvent.click(screen.getByTestId("signup-submit-btn"));
    await waitFor(() => expect(signupMock).toHaveBeenCalledTimes(1));
    const body = signupMock.mock.calls[0][0].body;
    expect(body).toMatchObject({
      email: "asha@example.com",
      name: "Asha Rao",
      password: "correct-horse",
      accepted_agreements: ["terms", "privacy"],
    });
  });

  it("links the agreement to the in-app legal pages", () => {
    render(<SignupFlow />);
    fillEmail();
    fillName();
    fillPassword();
    const hrefs = Array.from(document.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/trust");
    expect(hrefs).toContain("/privacy");
    expect(hrefs.some((href) => href?.includes("decibyl.ai"))).toBe(false);
  });

  it("returns a taken email to the email step with the reason", async () => {
    signupMock.mockResolvedValue({
      data: undefined,
      error: { detail: "Email already registered" },
      response: { status: 409 },
    });
    render(<SignupFlow />);
    fillEmail();
    fillName();
    fillPassword();
    fireEvent.click(screen.getByTestId("signup-agree-checkbox"));
    fireEvent.click(screen.getByTestId("signup-submit-btn"));
    await waitFor(() => expect(stepOf()).toBe("start"));
    expect(screen.getByRole("alert").textContent).toBe("Email already registered");
  });

  it("returns a refused invite to the invite step, even before the flag has answered", async () => {
    signupMock.mockResolvedValue({
      data: undefined,
      error: { detail: "That invite code is not valid." },
      response: { status: 403 },
    });
    render(<SignupFlow />);
    fillEmail();
    fillName();
    fillPassword();
    fireEvent.click(screen.getByTestId("signup-agree-checkbox"));
    fireEvent.click(screen.getByTestId("signup-submit-btn"));
    await waitFor(() => expect(stepOf()).toBe("invite"));
    expect(screen.getByRole("alert").textContent).toMatch(/invite code is not valid/);
  });

  it("asks for the invite code before leaving for Google", async () => {
    inviteOnly = true;
    const fetchMock = stubFetch();
    render(<SignupFlow />);
    await waitFor(() => expect(screen.getByTestId("google-signin-button")).toBeTruthy());
    fireEvent.click(screen.getByTestId("google-signin-button"));
    expect(stepOf()).toBe("invite");
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("start?"))).toBe(false);

    fillInvite("ABCD-2345");
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("invite=ABCD-2345"))).toBe(true),
    );
    // Google refused to start: the reason stays on the invite step.
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("stop here"));
    expect(stepOf()).toBe("invite");
  });
});
