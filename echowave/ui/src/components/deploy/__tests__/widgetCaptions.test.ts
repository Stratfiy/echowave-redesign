/**
 * Live captions, driven through the real widget.
 *
 * The server has always pushed `rtf-user-transcription` and `rtf-bot-text`
 * down the signalling socket — the in-app run view has rendered them for ages
 * — and the embed widget dropped them on the floor, so the share page had a
 * status line ("Listening — speak now") and no words.
 *
 * What earns tests is the merge, not the plumbing. The two sources behave
 * differently and neither can be appended blindly: the caller's transcription
 * arrives as interim guesses that a final result supersedes, and the bot's
 * arrives as separate sentences inside one turn. Append both and you get a
 * stutter; replace both and the bot loses half its sentence.
 *
 * Evaluated as a browser evaluates it, like `widgetPostCall.test.ts`, so these
 * run through the shipped file rather than a copy that could agree with itself.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const WIDGET_SOURCE = readFileSync(
    join(process.cwd(), "public/embed/decibyl-widget.js"),
    "utf-8"
);

type Caption = { role: string; text: string; final: boolean };

type WidgetApi = {
    start: () => Promise<void> | void;
    getCaptions: () => Caption[];
    onTranscript: (cb: (captions: Caption[]) => void) => void;
    getState: () => { config: { workflowId?: number } };
};

/**
 * Reached through a cast rather than `declare global`.
 *
 * `widgetPostCall.test.ts` already declares the same global with the slice of
 * the API it needs, and TypeScript requires every declaration of one global to
 * agree. Widening it there to satisfy this file would make that file's types
 * describe surface it does not use; declaring it differently here is an error.
 * A local view of the same object costs nothing and keeps each test honest
 * about what it touches.
 */
function widget(): WidgetApi {
    return (globalThis as unknown as { DecibylWidget: WidgetApi }).DecibylWidget;
}

/** The last `onmessage` the widget installed on its signalling socket. */
let deliver: ((raw: string) => void) | null = null;

/** Boot the widget and get it as far as an open signalling socket. */
async function bootAndConnect(): Promise<void> {
    document.head.innerHTML = "";
    document.body.innerHTML = "";
    deliver = null;

    const script = document.createElement("script");
    script.src =
        "https://app.decibyl.ai/embed/decibyl-widget.js?token=tok_test&apiEndpoint=https://api.decibyl.test";
    document.head.appendChild(script);

    vi.stubGlobal(
        "fetch",
        vi.fn(async () => ({
            ok: true,
            status: 200,
            // One stub serves both calls: /embed/config/<token> reads the top
            // level, /embed/init reads `config`.
            json: async () => ({
                workflow_id: 1,
                settings: {},
                session_token: "sess_test",
                workflow_run_id: 7,
                config: { workflow_id: 1 },
            }),
        }))
    );

    vi.stubGlobal("navigator", {
        ...globalThis.navigator,
        mediaDevices: {
            getUserMedia: vi.fn(async () => ({ getTracks: () => [] })),
        },
    });

    vi.stubGlobal(
        "RTCPeerConnection",
        class {
            signalingState = "stable";
            addTrack() {}
            addTransceiver() {}
            createOffer = async () => ({ type: "offer", sdp: "v=0" });
            setLocalDescription = async () => {};
            setRemoteDescription = async () => {};
            addIceCandidate = async () => {};
            getSenders = () => [];
            getReceivers = () => [];
            close() {}
        }
    );

    vi.stubGlobal(
        "WebSocket",
        class {
            static OPEN = 1;
            readyState = 1;
            onopen: (() => void) | null = null;
            onmessage: ((event: { data: string }) => void) | null = null;
            onerror: (() => void) | null = null;
            onclose: (() => void) | null = null;
            constructor() {
                setTimeout(() => {
                    // Captured before onopen so a message delivered from the
                    // open handler would still land.
                    deliver = (raw: string) => this.onmessage?.({ data: raw });
                    this.onopen?.();
                }, 0);
            }
            send() {}
            close() {}
        }
    );

    new Function(WIDGET_SOURCE)();

    await vi.waitFor(() => {
        if (!widget()?.getState()?.config?.workflowId) {
            throw new Error("config not merged yet");
        }
    });

    void widget().start();
    await vi.waitFor(() => {
        if (!deliver) throw new Error("signalling socket not open yet");
    });
}

function send(type: string, payload: Record<string, unknown> = {}) {
    deliver!(JSON.stringify({ type, payload }));
}

describe("live captions", () => {
    beforeEach(async () => {
        delete (globalThis as unknown as { DecibylWidget?: WidgetApi }).DecibylWidget;
        await bootAndConnect();
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("joins the bot's sentences into the one turn that spoke them", () => {
        send("rtf-bot-text", { text: "Sorry to hear that." });
        send("rtf-bot-text", { text: "Is it not opening, or not locking?" });

        const captions = widget().getCaptions();
        expect(captions).toHaveLength(1);
        expect(captions[0].text).toBe("Sorry to hear that. Is it not opening, or not locking?");
        expect(captions[0].role).toBe("bot");
    });

    it("starts a new line once the bot has stopped speaking", () => {
        send("rtf-bot-text", { text: "One moment." });
        send("rtf-bot-stopped-speaking");
        send("rtf-bot-text", { text: "Right, I have it." });

        const captions = widget().getCaptions();
        expect(captions.map((c) => c.text)).toEqual(["One moment.", "Right, I have it."]);
    });

    it("replaces the caller's interim guess rather than appending to it", () => {
        send("rtf-user-transcription", { text: "my lock", final: false });
        send("rtf-user-transcription", { text: "my lock is", final: false });
        send("rtf-user-transcription", { text: "my lock is stuck", final: true });

        const captions = widget().getCaptions();
        expect(captions).toHaveLength(1);
        // Not "my lock my lock is my lock is stuck".
        expect(captions[0].text).toBe("my lock is stuck");
        expect(captions[0].final).toBe(true);
    });

    it("keeps the two speakers on separate lines", () => {
        send("rtf-user-transcription", { text: "my lock is stuck", final: true });
        send("rtf-bot-text", { text: "Is it not opening?" });

        const captions = widget().getCaptions();
        expect(captions.map((c) => c.role)).toEqual(["user", "bot"]);
    });

    it("ignores an empty line rather than showing a blank row", () => {
        send("rtf-bot-text", { text: "   " });
        send("rtf-user-transcription", { text: "", final: true });

        expect(widget().getCaptions()).toHaveLength(0);
    });

    it("keeps only the last few turns", () => {
        for (let i = 0; i < 12; i += 1) {
            send("rtf-user-transcription", { text: `line ${i}`, final: true });
        }

        const captions = widget().getCaptions();
        // A running subtitle, not a transcript: the full record is on the run.
        expect(captions.length).toBeLessThanOrEqual(6);
        expect(captions[captions.length - 1].text).toBe("line 11");
    });

    it("tells a subscriber on every change", () => {
        const seen: Caption[][] = [];
        widget().onTranscript((captions) => seen.push(captions));

        send("rtf-user-transcription", { text: "hello", final: true });
        send("rtf-bot-text", { text: "Hello." });

        expect(seen).toHaveLength(2);
        expect(seen[1].map((c) => c.text)).toEqual(["hello", "Hello."]);
    });

    it("hands the subscriber a copy it cannot corrupt", () => {
        let received: Caption[] = [];
        widget().onTranscript((captions) => {
            received = captions;
        });

        send("rtf-user-transcription", { text: "hello", final: true });
        received[0].text = "tampered";
        send("rtf-bot-text", { text: "Hello." });

        expect(widget().getCaptions()[0].text).toBe("hello");
    });
});
