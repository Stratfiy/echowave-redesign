/**
 * Talk in Chat opens live voice only while `decibyl_voice` is on; off, the
 * entry point stays empty and Chat keeps its honest "not available yet".
 */

import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { hasEntryPoint, openEntryPoint, resetEntryPoints } from "@/lib/shell/chatEntryPoints";
import { INITIAL } from "@/lib/voice/sessionState";

const flags = vi.hoisted(() => ({ on: new Set<string>() }));
const start = vi.hoisted(() => vi.fn());

vi.mock("@/lib/features", () => ({ useFeature: (name: string) => flags.on.has(name) }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ getAccessToken: async () => "t" }) }));
vi.mock("@/lib/voice/useLiveVoice", () => ({
    useLiveVoice: () => ({
        state: INITIAL,
        inputLevel: 0,
        start,
        end: vi.fn(),
        toggleMute: vi.fn(),
        minimize: vi.fn(),
        close: vi.fn(),
    }),
}));

import { VoiceProvider } from "../VoiceProvider";

afterEach(() => {
    resetEntryPoints();
    flags.on.clear();
    start.mockReset();
});

describe("Talk", () => {
    it("is not registered while live voice is off", () => {
        render(<VoiceProvider />);
        expect(hasEntryPoint("talk")).toBe(false);
    });

    it("opens a live session with the thread and draft when on", () => {
        flags.on.add("decibyl_voice");
        render(<VoiceProvider />);
        expect(hasEntryPoint("talk")).toBe(true);
        act(() => {
            openEntryPoint("talk", { threadId: "t-9", draft: "remind me" });
        });
        expect(start).toHaveBeenCalledWith({ threadId: "t-9", draft: "remind me" });
    });

    it("unregisters when switched off again", () => {
        flags.on.add("decibyl_voice");
        const view = render(<VoiceProvider />);
        flags.on.clear();
        view.rerender(<VoiceProvider />);
        expect(hasEntryPoint("talk")).toBe(false);
    });
});
