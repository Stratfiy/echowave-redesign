import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type CatalogueOption, ModelSlotEditor } from "../ModelSlotEditor";

const api = { put: vi.fn() };
vi.mock("@/client/client.gen", () => ({ client: { put: (...args: unknown[]) => api.put(...args) } }));

const OPTIONS: CatalogueOption[] = [
    { provider: "sarvam", model: "saarika:v2.5", label: "Saarika", paise_per_minute: 50, approximate: false },
    { provider: "deepgram", model: "nova-3", label: "Nova 3", paise_per_minute: 90, approximate: true },
];

function editor(overrides: Partial<React.ComponentProps<typeof ModelSlotEditor>> = {}) {
    return (
        <ModelSlotEditor
            workflowId={7}
            component="stt"
            current={{ provider: "sarvam", model: "saarika:v2.5" }}
            options={OPTIONS}
            latencyMs={412}
            onSaved={() => {}}
            {...overrides}
        />
    );
}

beforeEach(() => {
    api.put.mockReset();
    api.put.mockResolvedValue({ data: {}, error: undefined });
    // jsdom has no ResizeObserver; Radix's dialog asks for one.
    vi.stubGlobal(
        "ResizeObserver",
        class {
            observe() {}
            unobserve() {}
            disconnect() {}
        },
    );
});

describe("ModelSlotEditor", () => {
    it("is a pencil that opens a side panel for the slot", () => {
        render(editor());
        expect(screen.queryByRole("dialog")).toBeNull();

        fireEvent.click(screen.getByRole("button", { name: "Change transcriber" }));

        // Radix's sheet is a dialog with the slot's name as its title, not a
        // popover: the list needs the height, and the tiles stay visible.
        const panel = screen.getByRole("dialog");
        expect(panel.getAttribute("data-slot")).toBe("sheet-content");
        expect(screen.getByText("Transcriber settings")).toBeTruthy();
        // The measured figure from the tile travels with it.
        expect(screen.getByText("412ms")).toBeTruthy();
    });

    it("opens on the model the tile shows and cannot save until something changes", () => {
        render(editor());
        fireEvent.click(screen.getByRole("button", { name: "Change transcriber" }));

        const current = screen.getByRole("radio", { name: /Saarika/ });
        expect(current.getAttribute("aria-checked")).toBe("true");
        expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("writes only this slot and closes when the save lands", async () => {
        const onSaved = vi.fn();
        render(editor({ onSaved }));
        fireEvent.click(screen.getByRole("button", { name: "Change transcriber" }));
        fireEvent.click(screen.getByRole("radio", { name: /Nova 3/ }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(onSaved).toHaveBeenCalled());
        expect(api.put).toHaveBeenCalledWith({
            url: "/api/v1/workflow/7/model-slot",
            body: { component: "stt", provider: "deepgram", model: "nova-3", voice: undefined },
        });
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    });

    it("stays open and shows the reason when the save is refused", async () => {
        api.put.mockResolvedValue({ data: undefined, error: { detail: "That model is not on offer." } });
        render(editor());
        fireEvent.click(screen.getByRole("button", { name: "Change transcriber" }));
        fireEvent.click(screen.getByRole("radio", { name: /Nova 3/ }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        expect(await screen.findByText("That model is not on offer.")).toBeTruthy();
        expect(screen.getByRole("dialog")).toBeTruthy();
    });

    it("sends the chosen voice with the voice slot", async () => {
        const onSaved = vi.fn();
        render(
            editor({
                component: "tts",
                current: { provider: "sarvam", model: "bulbul:v3" },
                options: [
                    { provider: "sarvam", model: "bulbul:v3", label: "Bulbul", paise_per_minute: 40, approximate: false },
                ],
                currentVoice: "anushka",
                voices: [
                    { voice_id: "anushka", name: "Anushka", gender: "female", description: null, is_default: true, sample_url: null, sample_url_hi: null },
                    { voice_id: "abhilash", name: "Abhilash", gender: "male", description: null, is_default: false, sample_url: null, sample_url_hi: null },
                ],
                onSaved,
            }),
        );
        fireEvent.click(screen.getByRole("button", { name: "Change voice" }));
        fireEvent.click(screen.getByText("Abhilash"));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(onSaved).toHaveBeenCalled());
        expect(api.put.mock.calls[0][0].body).toEqual({
            component: "tts",
            provider: "sarvam",
            model: "bulbul:v3",
            voice: "abhilash",
        });
    });
});

describe("the settings behind the pencil", () => {
    const configurations = {
        turn_stop_strategy: "turn_analyzer",
        smart_turn_stop_secs: 2,
        speak_like_callers: true,
    } as unknown as import("@/types/workflow-configurations").WorkflowConfigurations;

    it("shows the slot's own settings under the models", () => {
        render(editor({ configurations, onSaveConfigurations: vi.fn() }));
        fireEvent.click(screen.getByRole("button", { name: "Change transcriber" }));
        expect(screen.getByTestId("slot-settings")).toBeTruthy();
        // The transcriber's, not the brain's.
        expect(screen.getByText("Turn-taking")).toBeTruthy();
        expect(screen.queryByText("Temperature")).toBeNull();
    });

    it("saves a changed setting as a patch and leaves the model alone", async () => {
        const onSaveConfigurations = vi.fn().mockResolvedValue(undefined);
        const onSaved = vi.fn();
        render(editor({ configurations, onSaveConfigurations, onSaved }));
        fireEvent.click(screen.getByRole("button", { name: "Change transcriber" }));
        fireEvent.click(screen.getByRole("switch", { name: /Follow the caller/ }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(onSaved).toHaveBeenCalled());
        expect(onSaveConfigurations).toHaveBeenCalledWith({ follow_caller_language: true });
        // Nothing about the model changed, so the slot route is not called.
        expect(api.put).not.toHaveBeenCalled();
    });

    it("sends a moved temperature with the slot, not as a workflow patch", async () => {
        const onSaveConfigurations = vi.fn().mockResolvedValue(undefined);
        const onSaved = vi.fn();
        render(
            editor({
                component: "llm",
                current: { provider: "openai", model: "gpt-4.1" },
                options: [
                    { provider: "openai", model: "gpt-4.1", label: "GPT-4.1", paise_per_minute: 120, approximate: false },
                ],
                tuning: { temperature: 0.7 },
                configurations,
                onSaveConfigurations,
                onSaved,
            }),
        );
        fireEvent.click(screen.getByRole("button", { name: "Change brain" }));
        const slider = screen.getByRole("slider", { name: /Set|Vendor default/ });
        fireEvent.change(slider, { target: { value: "0.3" } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(onSaved).toHaveBeenCalled());
        expect(api.put.mock.calls[0][0].body).toEqual({
            component: "llm",
            provider: "openai",
            model: "gpt-4.1",
            voice: undefined,
            temperature: 0.3,
        });
        expect(onSaveConfigurations).not.toHaveBeenCalled();
    });

    it("stays open with the reason when the patch is refused", async () => {
        const onSaveConfigurations = vi.fn().mockRejectedValue(new Error("Not yours to change."));
        render(editor({ configurations, onSaveConfigurations }));
        fireEvent.click(screen.getByRole("button", { name: "Change transcriber" }));
        fireEvent.click(screen.getByRole("switch", { name: /Follow the caller/ }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        expect(await screen.findByText("Not yours to change.")).toBeTruthy();
        expect(screen.getByRole("dialog")).toBeTruthy();
    });
});
