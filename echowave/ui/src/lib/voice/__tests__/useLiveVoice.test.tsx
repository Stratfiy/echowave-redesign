/**
 * The live voice hook against fake browser media, WebRTC and sockets:
 * microphone permission states, an honest refusal before anything connects,
 * the offer on the voice socket, server messages moving the screen, mute
 * stopping the track, and End releasing the microphone and the connection.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    start: vi.fn(),
    end: vi.fn(),
    move: vi.fn(),
    get: vi.fn(),
    heartbeat: vi.fn(),
    turn: vi.fn(),
    record: vi.fn(),
}));

vi.mock("@/client/sdk.gen", () => ({
    startSessionApiV1VoiceSessionsPost: api.start,
    endSessionApiV1VoiceSessionsSessionIdEndPost: api.end,
    moveSessionApiV1VoiceSessionsSessionIdMovePost: api.move,
    getSessionApiV1VoiceSessionsSessionIdGet: api.get,
    heartbeatApiV1VoiceSessionsSessionIdHeartbeatPost: api.heartbeat,
    getTurnCredentialsApiV1TurnCredentialsGet: api.turn,
    recordTurnApiV1VoiceSessionsSessionIdTurnsPost: api.record,
}));
vi.mock("@/client/client.gen", () => ({ client: { getConfig: () => ({ baseUrl: "http://api.test" }) } }));
vi.mock("@/lib/apiClient", () => ({ resolveBrowserBackendUrl: () => "http://api.test" }));

import { onThreadStarted } from "@/lib/shell/chatEntryPoints";

import { useLiveVoice } from "../useLiveVoice";

class FakeTrack {
    enabled = true;
    stopped = false;
    stop() {
        this.stopped = true;
    }
}

class FakeStream {
    track = new FakeTrack();
    getTracks() {
        return [this.track];
    }
    getAudioTracks() {
        return [this.track];
    }
}

const sockets: FakeSocket[] = [];
class FakeSocket {
    static OPEN = 1;
    readyState = 1;
    sent: unknown[] = [];
    url: string;
    protocols: string[];
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: string }) => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    closed = false;
    constructor(url: string, protocols: string[]) {
        this.url = url;
        this.protocols = protocols;
        sockets.push(this);
        setTimeout(() => this.onopen?.(), 0);
    }
    send(data: string) {
        this.sent.push(JSON.parse(data));
    }
    close() {
        this.closed = true;
    }
    receive(message: unknown) {
        this.onmessage?.({ data: JSON.stringify(message) });
    }
}

class FakePeer {
    closed = false;
    connectionState = "new";
    localDescription: { sdp: string } | null = null;
    ontrack = null;
    onicecandidate = null;
    onconnectionstatechange = null;
    remote: unknown = null;
    addTrack() {}
    getSenders() {
        return [];
    }
    async createOffer() {
        return { type: "offer", sdp: "v=0 fake" };
    }
    async setLocalDescription(offer: { sdp: string }) {
        this.localDescription = offer;
    }
    async setRemoteDescription(answer: unknown) {
        this.remote = answer;
    }
    async addIceCandidate() {}
    close() {
        this.closed = true;
    }
}

let stream: FakeStream;
const getUserMedia = vi.fn();

beforeEach(() => {
    sockets.length = 0;
    stream = new FakeStream();
    getUserMedia.mockReset();
    getUserMedia.mockResolvedValue(stream);
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.stubGlobal("RTCPeerConnection", FakePeer);
    vi.stubGlobal("AudioContext", undefined);
    for (const fn of Object.values(api)) fn.mockReset();
    api.start.mockResolvedValue({ data: { id: 42, state_version: 1 } });
    api.end.mockResolvedValue({ data: { state: "ended" } });
    api.move.mockResolvedValue({ data: { state_version: 4 } });
    api.turn.mockResolvedValue({ data: { uris: [] } });
    api.heartbeat.mockResolvedValue({ data: {} });
});

afterEach(() => {
    vi.unstubAllGlobals();
});

function hook() {
    return renderHook(() => useLiveVoice({ getAccessToken: async () => "tok", measureLatency: true }));
}

describe("starting", () => {
    it("a blocked microphone is said, and no session is opened", async () => {
        getUserMedia.mockRejectedValue(Object.assign(new Error("no"), { name: "NotAllowedError" }));
        const { result } = hook();
        await act(() => result.current.start({ threadId: null, draft: "" }));
        expect(result.current.state.phase).toBe("mic_denied");
        expect(api.start).not.toHaveBeenCalled();
    });

    it("no microphone is said apart from a blocked one", async () => {
        getUserMedia.mockRejectedValue(Object.assign(new Error("no"), { name: "NotFoundError" }));
        const { result } = hook();
        await act(() => result.current.start({ threadId: null, draft: "" }));
        expect(result.current.state.phase).toBe("device_missing");
    });

    it("needs setup is refused before connecting, and the microphone is released", async () => {
        api.start.mockResolvedValue({
            error: { detail: { code: "needs_setup", message: "Live voice needs a voice before it can start.", next_step: "Ask an admin." } },
        });
        const { result } = hook();
        await act(() => result.current.start({ threadId: "t-1", draft: "" }));
        expect(result.current.state.phase).toBe("needs_setup");
        expect(result.current.state.notice).toBe("Live voice needs a voice before it can start. Ask an admin.");
        expect(stream.track.stopped).toBe(true);
        expect(sockets).toHaveLength(0);
    });

    it("connects on the voice socket with the token in the subprotocol and sends the offer", async () => {
        const { result } = hook();
        await act(() => result.current.start({ threadId: "t-1", draft: "" }));
        expect(api.start).toHaveBeenCalledWith({ body: { thread_id: "t-1" } });
        await waitFor(() => expect(sockets[0]?.sent).toHaveLength(1));
        expect(sockets[0].url).toBe("ws://api.test/api/v1/ws/voice/42");
        expect(sockets[0].protocols).toEqual(["decibyl.auth", "bearer.tok"]);
        expect(sockets[0].sent[0]).toMatchObject({ type: "offer", payload: { sdp: "v=0 fake", type: "offer" } });
        expect(result.current.state.phase).toBe("connecting");
    });
});

describe("the conversation it speaks in", () => {
    // Phase 3: Talk from Chat's start screen, where the original is not this
    // member's, speaks in a new conversation the server starts; Chat follows.
    it("announces a conversation the server started, so Chat can follow it", async () => {
        const started = vi.fn();
        const off = onThreadStarted(started);
        api.start.mockResolvedValue({ data: { id: 42, state_version: 1, thread_id: "t-new" } });
        const { result } = hook();
        await act(() => result.current.start({ threadId: null, draft: "" }));
        expect(started).toHaveBeenCalledWith("t-new");
        off();
    });

    it("says nothing when the session is in the conversation already on screen", async () => {
        const started = vi.fn();
        const off = onThreadStarted(started);
        api.start.mockResolvedValue({ data: { id: 42, state_version: 1, thread_id: "t-1" } });
        const { result } = hook();
        await act(() => result.current.start({ threadId: "t-1", draft: "" }));
        expect(started).not.toHaveBeenCalled();
        off();
    });
});

describe("in a session", () => {
    async function live() {
        const view = hook();
        await act(() => view.result.current.start({ threadId: null, draft: "" }));
        await waitFor(() => expect(sockets[0]?.sent).toHaveLength(1));
        act(() => sockets[0].receive({ type: "voice-live", payload: { session: { state_version: 3 }, gap_sentence: null } }));
        return view;
    }

    it("server messages move the screen and fill captions", async () => {
        const { result } = await live();
        expect(result.current.state.phase).toBe("listening");
        act(() => sockets[0].receive({ type: "rtf-user-transcription", payload: { text: "hello", final: true } }));
        act(() => sockets[0].receive({ type: "voice-phase", payload: { phase: "processing" } }));
        expect(result.current.state.phase).toBe("processing");
        act(() => sockets[0].receive({ type: "rtf-bot-started-speaking", payload: {} }));
        act(() => sockets[0].receive({ type: "rtf-bot-text", payload: { text: "Hi there." } }));
        expect(result.current.state.phase).toBe("speaking");
        expect(result.current.state.captions.map((c) => c.text)).toEqual(["hello", "Hi there."]);
        act(() => sockets[0].receive({ type: "rtf-bot-stopped-speaking", payload: {} }));
        expect(result.current.state.phase).toBe("listening");
    });

    it("mute stops the track sending and is recorded on the session", async () => {
        const { result } = await live();
        await act(() => result.current.toggleMute());
        expect(stream.track.enabled).toBe(false);
        expect(api.move).toHaveBeenCalledWith({ path: { session_id: 42 }, body: { expected_version: 3, to: "live", muted: true } });
        expect(result.current.state.muted).toBe(true);
    });

    it("End ends the session and releases the microphone and connection", async () => {
        const { result } = await live();
        await act(() => result.current.end());
        expect(api.end).toHaveBeenCalledWith({ path: { session_id: 42 }, body: { reason: "user_ended" } });
        expect(stream.track.stopped).toBe(true);
        expect(sockets[0].closed).toBe(true);
        expect(result.current.state.phase).toBe("ended");
    });

    it("a server refusal mid-session ends it with the reason", async () => {
        const { result } = await live();
        await act(async () => {
            sockets[0].receive({ type: "error", payload: { error_type: "voice_limit_reached", message: "You used 15 of 15 voice minutes." } });
        });
        await waitFor(() => expect(result.current.state.phase).toBe("limit_reached"));
        expect(api.end).toHaveBeenCalledWith({ path: { session_id: 42 }, body: { reason: "voice_limit_reached" } });
    });

    it("a closed turn with no measurement reports nothing rather than zero", async () => {
        await live();
        await act(async () => {
            sockets[0].receive({ type: "voice-turn-closed", payload: { turn_index: 0, interrupted: false, tool_turn: true } });
        });
        expect(api.record).not.toHaveBeenCalled();
    });
});

describe("another kind of session (a huddle)", () => {
    it("starts, connects and ends on the routes it is given, and hands on what it does not handle", async () => {
        const routes = {
            start: vi.fn(async () => ({ data: { id: 9, state: "connecting", state_version: 1 } })),
            get: vi.fn(async () => ({ data: { id: 9, state: "live", state_version: 2 } })),
            move: vi.fn(async () => ({ data: { id: 9, state: "live", state_version: 3 } })),
            heartbeat: vi.fn(async () => ({ data: {} })),
            end: vi.fn(async () => ({ data: {} })),
            socketPath: (id: number) => `/api/v1/ws/huddle/${id}`,
        };
        const events: Array<[string, Record<string, unknown>]> = [];
        const { result } = renderHook(() =>
            useLiveVoice({
                getAccessToken: async () => "tok",
                measureLatency: false,
                api: routes,
                onServerEvent: (type, payload) => events.push([type, payload]),
            }),
        );
        await act(() => result.current.start({ threadId: null, draft: "" }));
        expect(routes.start).toHaveBeenCalled();
        expect(api.start).not.toHaveBeenCalled();
        await waitFor(() => expect(sockets[0]?.sent).toHaveLength(1));
        expect(sockets[0].url).toBe("ws://api.test/api/v1/ws/huddle/9");
        await act(async () => {
            sockets[0].receive({ type: "huddle-card", payload: { event_id: 77 } });
        });
        expect(events).toEqual([["huddle-card", { event_id: 77 }]]);
        await act(() => result.current.end());
        expect(routes.end).toHaveBeenCalledWith(9, "user_ended");
        expect(api.end).not.toHaveBeenCalled();
    });
});
