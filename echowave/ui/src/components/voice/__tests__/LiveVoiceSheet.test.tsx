/**
 * Screen 05: one real meter only while capturing, a state label, captions,
 * End always there and distinct, mute, minimise to a strip, and Continue in
 * text when voice cannot run.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { INITIAL, type VoiceState } from "@/lib/voice/sessionState";

import { LiveVoiceSheet } from "../LiveVoiceSheet";

function sheet(state: Partial<VoiceState>, level = 0.1) {
    const handlers = {
        onEnd: vi.fn(),
        onToggleMute: vi.fn(),
        onMinimize: vi.fn(),
        onRetry: vi.fn(),
        onClose: vi.fn(),
    };
    render(<LiveVoiceSheet state={{ ...INITIAL, ...state }} inputLevel={level} {...handlers} />);
    return handlers;
}

describe("the live session sheet", () => {
    it("draws nothing while idle", () => {
        sheet({ phase: "idle" });
        expect(screen.queryByTestId("voice-sheet")).toBeNull();
    });

    it("listening: label, microphone on, a real meter, Mute and End", () => {
        const h = sheet({ phase: "listening", sessionId: 1 }, 0.1);
        expect(screen.getByTestId("voice-state-label").textContent).toBe("Listening");
        expect(screen.getByTestId("mic-indicator").textContent).toContain("Microphone on");
        const meter = screen.getByRole("meter", { name: "Microphone level" });
        expect(meter.getAttribute("aria-valuenow")).toBe("50");
        fireEvent.click(screen.getByTestId("voice-end"));
        expect(h.onEnd).toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: "Mute" }));
        expect(h.onToggleMute).toHaveBeenCalled();
    });

    it("muted: no meter, microphone off, Unmute", () => {
        sheet({ phase: "listening", muted: true });
        expect(screen.queryByTestId("input-meter")).toBeNull();
        expect(screen.getByTestId("mic-indicator").textContent).toContain("Microphone off");
        expect(screen.getByRole("button", { name: "Unmute" }).getAttribute("aria-pressed")).toBe("true");
    });

    it("connecting shows no meter yet, but End is already there", () => {
        sheet({ phase: "connecting" });
        expect(screen.queryByTestId("input-meter")).toBeNull();
        expect(screen.getByTestId("voice-end")).toBeTruthy();
    });

    it("captions show both sides and can be hidden", () => {
        sheet({
            phase: "speaking",
            captions: [
                { id: "1", who: "you", text: "When is my meeting?", final: true },
                { id: "2", who: "decibyl", text: "At four.", final: false },
            ],
        });
        expect(screen.getByText("When is my meeting?")).toBeTruthy();
        expect(screen.getByText("At four.")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Hide captions" }));
        expect(screen.queryByText("At four.")).toBeNull();
    });

    it("an approval waits in Chat, never by voice", () => {
        const h = sheet({ phase: "listening", approvalWaiting: true });
        expect(screen.getByTestId("voice-approval").textContent).toContain("Nothing happens until you tap it");
        fireEvent.click(screen.getByRole("button", { name: "Review it" }));
        expect(h.onMinimize).toHaveBeenCalledWith(true);
    });

    it.each([
        ["mic_denied", "Microphone blocked", true],
        ["needs_setup", "Voice needs setup", false],
        ["limit_reached", "Daily voice limit reached", false],
        ["ended", "Ended", true],
    ] as const)("%s offers Continue in text (retry: %s)", (phase, label, retry) => {
        const h = sheet({ phase, notice: "Why it stopped." });
        expect(screen.getByTestId("voice-state-label").textContent).toBe(label);
        expect(screen.getByTestId("voice-notice").textContent).toBe("Why it stopped.");
        expect(screen.queryByTestId("voice-end")).toBeNull();
        expect(Boolean(screen.queryByRole("button", { name: "Try again" }))).toBe(retry);
        fireEvent.click(screen.getByTestId("voice-continue-text"));
        expect(h.onClose).toHaveBeenCalled();
    });

    it("minimised keeps a strip with Open and End", () => {
        const h = sheet({ phase: "speaking", minimized: true });
        expect(screen.queryByTestId("voice-sheet")).toBeNull();
        expect(screen.getByTestId("voice-strip").textContent).toContain("Decibyl is speaking");
        fireEvent.click(screen.getByRole("button", { name: "Open" }));
        expect(h.onMinimize).toHaveBeenCalledWith(false);
        fireEvent.click(screen.getByRole("button", { name: "End voice session" }));
        expect(h.onEnd).toHaveBeenCalled();
    });
});
