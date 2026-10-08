/**
 * The save contract on the person's own settings (screens 17-19; handoff 30):
 * dirty -> saving -> saved; a rejection keeps the draft; a revision conflict
 * shows both versions and never overwrites; a failed load is an error, not
 * empty settings; long instructions are refused, never cut.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), voices: vi.fn(), start: vi.fn() }));
const flags = vi.hoisted(() => ({ value: {} as Record<string, boolean> }));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false, logout: vi.fn() }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(flags.value[name]) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/settings" }));
vi.mock("@/components/MfaSection", () => ({ MfaSection: () => <p>MFA controls</p> }));
vi.mock("@/components/ThemeModeSection", () => ({ ThemeModeSection: () => <p>Theme controls</p> }));
vi.mock("@/client/sdk.gen", () => ({
  myProfileApiV1MeSettingsProfileGet: api.get,
  saveMyProfileApiV1MeSettingsProfilePut: api.put,
  myVoicesApiV1MeSettingsVoicesGet: api.voices,
  myQuotasApiV1MeQuotasGet: vi.fn().mockResolvedValue({ data: { allowances: [] } }),
  startTemporaryConversationApiV1MeTemporaryConversationsPost: api.start,
}));

import { AccountSettings } from "../AccountSettings";
import { PersonalizationSettings } from "../PersonalizationSettings";
import { VoiceSettings } from "../VoiceSettings";

const LANGS = [
  { tag: "en-IN", native: "English", english: "English", voice: true },
  { tag: "ta-IN", native: "தமிழ்", english: "Tamil", voice: true },
  { tag: "ur-IN", native: "اردو", english: "Urdu", voice: false },
];

function profile(extra: Record<string, unknown> = {}) {
  return {
    revision: 3,
    language: "en-IN",
    response_length: "balanced",
    custom_instructions: null,
    explanation_language: null,
    preferred_name: "Nithya",
    timezone: "Asia/Kolkata",
    email: "nithya@clinic.in",
    email_verified: true,
    voice: "anushka",
    speaking_speed: 1,
    captions: true,
    auto_detect_language: false,
    memory_enabled: false,
    languages: LANGS,
    max_instructions: 20,
    ...extra,
  };
}

beforeEach(() => {
  flags.value = { memory_manager: true };
  api.get.mockResolvedValue({ data: profile() });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Personalization", () => {
  it("saves only what changed, with the revision it read, and reloads to what is saved", async () => {
    api.put.mockResolvedValue({ data: profile({ revision: 4, response_length: "short" }), response: { status: 200 } });
    render(<PersonalizationSettings />);
    fireEvent.click(await screen.findByRole("radio", { name: /Short/ }));
    expect(screen.getByTestId("save-bar").getAttribute("data-state")).toBe("dirty");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith({ body: { revision: 3, response_length: "short" } }));
    await waitFor(() => expect(screen.getByTestId("save-bar").getAttribute("data-state")).toBe("saved"));
  });

  it("a conflict shows both versions and Keep mine saves over the new revision", async () => {
    api.put
      .mockResolvedValueOnce({
        error: { detail: { message: "Changed elsewhere", stored: profile({ revision: 5, response_length: "detailed" }) } },
        response: { status: 409 },
      })
      .mockResolvedValueOnce({ data: profile({ revision: 6, response_length: "short" }), response: { status: 200 } });
    render(<PersonalizationSettings />);
    fireEvent.click(await screen.findByRole("radio", { name: /Short/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const notice = await screen.findByTestId("settings-conflict");
    expect(within(notice).getByText("short")).toBeTruthy();
    expect(within(notice).getByText("detailed")).toBeTruthy();
    fireEvent.click(within(notice).getByRole("button", { name: "Keep mine" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.put).toHaveBeenLastCalledWith({ body: { revision: 5, response_length: "short" } }));
  });

  it("a rejection keeps the draft and says why", async () => {
    api.put.mockResolvedValue({ error: { detail: "That language is not offered yet." }, response: { status: 422 } });
    render(<PersonalizationSettings />);
    fireEvent.click(await screen.findByRole("radio", { name: /Detailed/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("That language is not offered yet.")).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Detailed/ }).getAttribute("aria-checked")).toBe("true");
  });

  it("long instructions are flagged with the limit, not cut", async () => {
    render(<PersonalizationSettings />);
    const box = await screen.findByPlaceholderText(/For example/);
    fireEvent.change(box, { target: { value: "x".repeat(25) } });
    expect((box as HTMLTextAreaElement).value).toHaveLength(25);
    expect(screen.getByText(/Up to 20 characters; this is 25/)).toBeTruthy();
  });

  it("languages are searchable by their own name", async () => {
    render(<PersonalizationSettings />);
    await screen.findByText("Language and answer style");
    const search = screen.getAllByPlaceholderText(/English|Search languages/)[0];
    fireEvent.change(search, { target: { value: "தமி" } });
    const options = screen.getByTestId("reply-language-options");
    expect(within(options).getByText("Tamil")).toBeTruthy();
    expect(within(options).queryByText("Urdu")).toBeNull();
  });

  it("a failed load is an error with retry, never empty settings", async () => {
    api.get.mockResolvedValueOnce({ error: { detail: "boom" } }).mockResolvedValueOnce({ data: profile() });
    render(<PersonalizationSettings />);
    expect(await screen.findByText("Could not load your settings")).toBeTruthy();
    expect(screen.queryByRole("radio", { name: /Short/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));
    expect(await screen.findByRole("radio", { name: /Short/ })).toBeTruthy();
  });

  it("offers a temporary conversation and says what it keeps", async () => {
    render(<PersonalizationSettings />);
    expect(await screen.findByTestId("temporary-conversation")).toBeTruthy();
    expect(screen.getByText(/saves nothing to memory/)).toBeTruthy();
  });
});

describe("Account", () => {
  it("shows the verified email, the person's own timezone and an honest devices row", async () => {
    render(<AccountSettings />);
    expect(await screen.findByText("nithya@clinic.in")).toBeTruthy();
    expect(screen.getByText("Verified")).toBeTruthy();
    expect((screen.getByLabelText("Your timezone") as HTMLSelectElement).value).toBe("Asia/Kolkata");
    expect(screen.getByTestId("devices-unavailable").textContent).toMatch(/Unavailable/);
    // Two-step sign-in is here only while Privacy and security is off.
    expect(screen.getByText("MFA controls")).toBeTruthy();
  });
});

describe("Voice and language", () => {
  it("says a needed key is missing rather than pretending, and labels the new-session effect", async () => {
    api.voices.mockResolvedValue({
      data: {
        provider: "sarvam",
        provider_label: "Sarvam",
        language: "en-IN",
        language_supported: true,
        readiness: "needs_setup",
        readiness_reason: "Decibyl's Sarvam key is not set up on this deployment, so voice cannot speak yet.",
        voices: [{ voice_id: "anushka", name: "Anushka", gender: "female", sample_url: null }],
      },
    });
    render(<VoiceSettings />);
    expect(await screen.findByText(/Needs setup:/)).toBeTruthy();
    expect(screen.getByTestId("new-session-note").textContent).toMatch(/next voice session/);
    expect(screen.getByText("No sample yet")).toBeTruthy();
  });

  it("a slow answer for the old language never replaces the new one", async () => {
    let releaseOld: (v: unknown) => void = () => {};
    api.voices
      .mockReturnValueOnce(new Promise((resolve) => (releaseOld = resolve)))
      .mockResolvedValueOnce({
        data: { provider: "sarvam", provider_label: "Sarvam", readiness: "ready", language_supported: true, voices: [{ voice_id: "kavya", name: "Kavya" }] },
      });
    render(<VoiceSettings />);
    const options = await screen.findByTestId("spoken-language-options");
    fireEvent.click(within(options).getByText("Tamil"));
    expect(await screen.findByText("Kavya")).toBeTruthy();
    releaseOld({ data: { provider: "sarvam", provider_label: "Sarvam", readiness: "ready", voices: [{ voice_id: "old", name: "Stale voice" }] } });
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByText("Stale voice")).toBeNull();
  });
});
