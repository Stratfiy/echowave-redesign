/**
 * The live voice state machine (screen 05): idle -> microphone -> connecting
 * -> listening <-> processing <-> speaking, the named failure states, and
 * nothing moving an ended session.
 */

import { describe, expect, it } from "vitest";

import { ACTIVE, FINAL, INITIAL, LABEL, reduce, type VoiceEvent, type VoiceState } from "../sessionState";

function run(...events: VoiceEvent[]): VoiceState {
    return events.reduce(reduce, INITIAL);
}

const LIVE: VoiceEvent[] = [
    { type: "open" },
    { type: "mic_requested" },
    { type: "session_started", sessionId: 7, stateVersion: 1 },
    { type: "live", stateVersion: 2 },
];

describe("the happy path", () => {
    it("goes idle, microphone, connecting, listening", () => {
        expect(run({ type: "open" }).phase).toBe("idle");
        expect(run({ type: "open" }, { type: "mic_requested" }).phase).toBe("requesting_mic");
        expect(run(...LIVE.slice(0, 3)).phase).toBe("connecting");
        const live = run(...LIVE);
        expect(live.phase).toBe("listening");
        expect(live.sessionId).toBe(7);
        expect(live.stateVersion).toBe(2);
    });

    it("thinks, speaks and listens again", () => {
        expect(run(...LIVE, { type: "processing" }).phase).toBe("processing");
        expect(run(...LIVE, { type: "processing" }, { type: "bot_started" }).phase).toBe("speaking");
        expect(run(...LIVE, { type: "bot_started" }, { type: "bot_stopped" }).phase).toBe("listening");
    });

    it("labels every phase", () => {
        for (const phase of [...ACTIVE, ...FINAL]) expect(LABEL[phase]).toBeTruthy();
        expect(LABEL.speaking).toBe("Decibyl is speaking");
    });
});

describe("recoverable and final states", () => {
    it.each([
        ["mic_denied", { type: "mic_denied" } as VoiceEvent],
        ["device_missing", { type: "mic_missing" } as VoiceEvent],
        ["connect_timeout", { type: "connect_timeout" } as VoiceEvent],
    ])("%s is said", (phase, event) => {
        const state = run({ type: "open" }, { type: "mic_requested" }, event);
        expect(state.phase).toBe(phase);
        expect(state.notice).toBeTruthy();
    });

    it.each([
        ["needs_setup", "needs_setup"],
        ["voice_limit_reached", "limit_reached"],
        ["already_live", "already_live"],
        ["something_new", "failed"],
    ])("a refusal %s becomes %s with the server's words", (code, phase) => {
        const state = run({ type: "open" }, { type: "refused", code, message: "Because." });
        expect(state.phase).toBe(phase);
        expect(state.notice).toBe("Because.");
    });

    it("reconnects and repeats how much audio was lost", () => {
        const lost = run(...LIVE, { type: "connection_lost" });
        expect(lost.phase).toBe("reconnecting");
        const back = reduce(lost, {
            type: "live",
            gapSentence: "Reconnected. About 4 seconds of audio was lost; anything you said then was not heard.",
        });
        expect(back.phase).toBe("listening");
        expect(back.notice).toContain("4 seconds");
    });

    it("ignores speech events while reconnecting", () => {
        const lost = run(...LIVE, { type: "connection_lost" });
        expect(reduce(lost, { type: "bot_started" }).phase).toBe("reconnecting");
    });

    it("never moves an ended session", () => {
        const ended = run(...LIVE, { type: "ended" });
        expect(reduce(ended, { type: "bot_started" }).phase).toBe("ended");
        expect(reduce(ended, { type: "user_caption", text: "late", final: true }).captions).toEqual([]);
        expect(reduce(ended, { type: "open" }).phase).toBe("idle");
    });
});

describe("captions and approvals", () => {
    it("grows an utterance in place until final, then starts the next", () => {
        const state = run(
            ...LIVE,
            { type: "user_caption", text: "when is", final: false },
            { type: "user_caption", text: "when is my meeting", final: true },
            { type: "bot_caption", text: "At " },
            { type: "bot_caption", text: "four." },
            { type: "turn_closed", toolTurn: false },
        );
        expect(state.captions.map((c) => [c.who, c.text, c.final])).toEqual([
            ["you", "when is my meeting", true],
            ["decibyl", "At four.", true],
        ]);
    });

    it("a turn that proposed a card says an approval is waiting", () => {
        expect(run(...LIVE, { type: "turn_closed", toolTurn: true }).approvalWaiting).toBe(true);
        expect(run(...LIVE, { type: "turn_closed", toolTurn: false }).approvalWaiting).toBe(false);
    });

    it("records mute", () => {
        expect(run(...LIVE, { type: "muted", muted: true, stateVersion: 3 })).toMatchObject({ muted: true, stateVersion: 3 });
    });
});
