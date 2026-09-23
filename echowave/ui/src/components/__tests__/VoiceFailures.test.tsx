import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const read = vi.fn();
const features: Record<string, boolean> = {};

vi.mock("@/client/sdk.gen", () => ({
    readVoiceFailuresApiV1AdminProviderKeysVoiceFailuresGet: () => read(),
}));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(features[name]) }));

import { describeFailure, type VoiceFailure, VoiceFailures } from "../VoiceFailures";

const now = new Date("2026-09-23T18:00:00Z");

function failure(overrides: Partial<VoiceFailure> = {}): VoiceFailure {
    return {
        provider: "elevenlabs",
        count: 3,
        last_at: "2026-09-23T17:48:00Z",
        last_run_id: 411,
        last_model: "eleven_flash_v2_5",
        ...overrides,
    };
}

describe("describeFailure", () => {
    it("names the voice, how many calls, when, and which call", () => {
        const text = describeFailure(failure(), now);
        expect(text).toContain("eleven_flash_v2_5");
        expect(text).toContain("3 calls got no audio back");
        expect(text).toContain("12 min ago");
        expect(text).toContain("run 411");
    });

    it("says one call, not one calls", () => {
        expect(describeFailure(failure({ count: 1 }), now)).toContain("1 call got");
    });

    it("counts in hours past the hour", () => {
        expect(describeFailure(failure({ last_at: "2026-09-23T15:00:00Z" }), now)).toContain("3 h ago");
    });
});

describe("VoiceFailures", () => {
    beforeEach(() => {
        read.mockReset();
        for (const k of Object.keys(features)) delete features[k];
    });

    it("asks nothing and shows nothing while the switch is off", () => {
        const { container } = render(<VoiceFailures />);
        expect(container.innerHTML).toBe("");
        expect(read).not.toHaveBeenCalled();
    });

    it("shows a red warning for a voice that went quiet", async () => {
        features.voice_watch = true;
        read.mockResolvedValue({ data: { failures: [failure()] } });

        render(<VoiceFailures />);

        const alert = await screen.findByRole("alert");
        expect(alert.textContent).toContain("A voice provider is not answering on calls");
        expect(alert.textContent).toContain("got no audio back");
    });

    it("stays out of the way when every voice is answering", async () => {
        features.voice_watch = true;
        read.mockResolvedValue({ data: { failures: [] } });

        const { container } = render(<VoiceFailures />);

        await waitFor(() => expect(read).toHaveBeenCalled());
        expect(container.innerHTML).toBe("");
    });
});
