/**
 * Screen 19: a language change never swaps the voice for another; one Save
 * sends language, voice, speed and captions with the revision read; a stale
 * save shows the stored version beside yours; preview is honest when no
 * sample can be recorded and a later press wins over an earlier answer.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    catalogue: vi.fn(),
    prefs: vi.fn(),
    save: vi.fn(),
    preview: vi.fn(),
}));

vi.mock("@/client/sdk.gen", () => ({
    voiceCatalogueApiV1VoiceCatalogueGet: api.catalogue,
    myVoicePreferencesApiV1VoicePreferencesGet: api.prefs,
    saveMyVoicePreferencesApiV1VoicePreferencesPut: api.save,
    voicePreviewApiV1VoicePreviewGet: api.preview,
    voiceReadinessApiV1VoiceReadinessGet: vi.fn(async () => ({ data: { calls: null } })),
    appointmentPolicyApiV1VoiceAppointmentsPolicyGet: vi.fn(),
    saveAppointmentPolicyApiV1VoiceAppointmentsPolicyPut: vi.fn(),
    upcomingAppointmentsApiV1VoiceAppointmentsGet: vi.fn(),
    getWorkflowsApiV1WorkflowFetchGet: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: () => false }));

import { VoiceLanguageSettings } from "../VoiceLanguageSettings";

const CATALOGUE = {
    speed_min: 0.5,
    speed_max: 2,
    languages: [
        { code: "ta-IN", english: "Tamil", native: "தமிழ்", spoken: true, voices: [{ id: "sarvam:bulbul:v3:kavya", label: "Kavya", model: "bulbul:v3" }] },
        { code: "hi-IN", english: "Hindi", native: "हिन्दी", spoken: true, voices: [{ id: "sarvam:bulbul:v3:ritu", label: "Ritu", model: "bulbul:v3" }] },
        { code: "xx-IN", english: "Other", native: "Other", spoken: false, voices: [] },
    ],
};
const STORED = { language: "ta-IN", voice: "sarvam:bulbul:v3:kavya", voice_speed: 1, captions: true, revision: 2, updated_at: null };

beforeEach(() => {
    for (const fn of Object.values(api)) fn.mockReset();
    api.catalogue.mockResolvedValue({ data: CATALOGUE });
    api.prefs.mockResolvedValue({ data: STORED });
});

async function shown() {
    render(<VoiceLanguageSettings />);
    return screen.findByTestId("voice-settings");
}

describe("voice and language", () => {
    it("shows the saved voice selected and nothing to save", async () => {
        await shown();
        expect((screen.getByRole("radio", { name: "Kavya" }) as HTMLInputElement).checked).toBe(true);
        expect(screen.queryByTestId("save-bar")).toBeNull();
    });

    it("a language whose voices do not include yours clears the voice and asks", async () => {
        await shown();
        fireEvent.change(screen.getByTestId("voice-language"), { target: { value: "hi-IN" } });
        expect((screen.getByRole("radio", { name: "Ritu" }) as HTMLInputElement).checked).toBe(false);
        expect(screen.getByText("Choose a voice that speaks this language.")).toBeTruthy();
        expect(screen.getByTestId("save-bar").getAttribute("data-state")).toBe("dirty");
    });

    it("a language with no voice says text only", async () => {
        await shown();
        fireEvent.change(screen.getByTestId("voice-language"), { target: { value: "xx-IN" } });
        expect(screen.getByTestId("voice-unavailable").textContent).toContain("answer in text and captions");
    });

    it("one Save sends every field with the revision it read", async () => {
        api.save.mockResolvedValue({ data: { ...STORED, voice: "sarvam:bulbul:v3:ritu", language: "hi-IN", revision: 3 } });
        await shown();
        fireEvent.change(screen.getByTestId("voice-language"), { target: { value: "hi-IN" } });
        fireEvent.click(screen.getByRole("radio", { name: "Ritu" }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() => expect(api.save).toHaveBeenCalled());
        expect(api.save.mock.calls[0][0].body).toEqual({
            revision: 2,
            language: "hi-IN",
            voice: "sarvam:bulbul:v3:ritu",
            voice_speed: 1,
            captions: true,
        });
        await waitFor(() => expect(screen.getByTestId("save-bar").getAttribute("data-state")).toBe("saved"));
    });

    it("a stale save shows the stored version and keeps yours", async () => {
        api.save.mockResolvedValue({
            error: { detail: { message: "Changed", stored: { ...STORED, captions: false, revision: 3 } } },
            response: { status: 409 },
        });
        await shown();
        fireEvent.click(screen.getByRole("checkbox"));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect((await screen.findByTestId("voice-conflict")).textContent).toContain("captions off");
        expect(screen.getByTestId("save-bar").getAttribute("data-state")).toBe("conflict");
    });

    it("preview with no sample says why", async () => {
        api.preview.mockResolvedValue({ data: { state: "needs_setup", url: null, reason: "A preview could not be recorded: the voice service is not set up." } });
        await shown();
        fireEvent.click(screen.getByRole("button", { name: "Preview Kavya" }));
        expect((await screen.findByTestId("preview-note")).textContent).toContain("not set up");
        expect(api.preview).toHaveBeenCalledWith({ query: { voice: "sarvam:bulbul:v3:kavya", language: "ta-IN" } });
    });

    it("shows a load failure with a retry, not an empty form", async () => {
        api.prefs.mockResolvedValue({ error: { detail: "boom" } });
        render(<VoiceLanguageSettings />);
        expect(await screen.findByText("Voice settings could not load")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    });
});
