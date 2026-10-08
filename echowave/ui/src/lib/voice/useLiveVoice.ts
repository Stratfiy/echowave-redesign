"use client";

/**
 * Live voice with Decibyl in the browser (screen 05; handoff 7 mobile, 12, 21).
 *
 * The microphone is asked for first, so a denial is said before anything
 * starts on the server; then a session is opened (which refuses honestly
 * when voice needs setup or the day's minutes are spent), the audio connects
 * over WebRTC with the same signaling the workflow tester uses, and the
 * server's caption and turn messages drive the screen's state machine.
 *
 * - **Mute** stops the track sending audio and is recorded on the session.
 * - **End** always works: it ends the session, closes the connection and
 *   stops every track, so the browser's microphone light goes out.
 * - **Reconnect** after a dropped connection tries a few times with backoff,
 *   on the same session; the server says how much audio was lost and the
 *   screen repeats it. Nothing the person said is replayed, so no task is
 *   duplicated.
 * - **Latency** (handoff 12) is measured here, on one clock: the person's
 *   speech end (the input level falling) to Decibyl's first audible reply
 *   (the playback level rising), and an interruption to playback stopping.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import {
    endSessionApiV1VoiceSessionsSessionIdEndPost,
    getSessionApiV1VoiceSessionsSessionIdGet,
    getTurnCredentialsApiV1TurnCredentialsGet,
    heartbeatApiV1VoiceSessionsSessionIdHeartbeatPost,
    moveSessionApiV1VoiceSessionsSessionIdMovePost,
    recordTurnApiV1VoiceSessionsSessionIdTurnsPost,
    startSessionApiV1VoiceSessionsPost,
} from "@/client/sdk.gen";
import { resolveBrowserBackendUrl } from "@/lib/apiClient";
import { announceThreadStarted } from "@/lib/shell/chatEntryPoints";

import { ACTIVE, INITIAL, reduce, type VoiceState } from "./sessionState";

export const CONNECT_TIMEOUT_MS = 15_000;
export const HEARTBEAT_MS = 20_000;
export const RECONNECT_DELAYS_MS = [1_000, 2_000, 4_000];
/** Input level above which the person is speaking, and below which they stopped. */
const SPEAKING = 0.06;
const QUIET = 0.03;
/** Silence this long ends the person's speech. */
const SPEECH_END_MS = 250;
/** Playback above this is audible. */
const AUDIBLE = 0.02;

type Refusal = { code: string; message: string };

export type StartContext = { threadId: string | null; draft: string };

function refusalOf(error: unknown, fallback: string): Refusal {
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
        const d = detail as { code?: string; message?: string; next_step?: string };
        return {
            code: d.code ?? "failed",
            message: [d.message, d.next_step].filter(Boolean).join(" ") || fallback,
        };
    }
    if (typeof detail === "string") return { code: "failed", message: detail };
    return { code: "failed", message: fallback };
}

function level(analyser: AnalyserNode | null, buffer: Float32Array<ArrayBuffer> | null): number {
    if (!analyser || !buffer) return 0;
    analyser.getFloatTimeDomainData(buffer);
    let sum = 0;
    for (let i = 0; i < buffer.length; i += 1) sum += buffer[i] * buffer[i];
    return Math.sqrt(sum / buffer.length);
}

function newPcId(): string {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return `PC-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

export function useLiveVoice({
    getAccessToken,
    measureLatency,
}: {
    getAccessToken: () => Promise<string | null | undefined>;
    /** Whether to report per-turn timings (flag `voice_latency`). */
    measureLatency: boolean;
}) {
    const [state, dispatch] = useReducer(reduce, INITIAL);
    const [inputLevel, setInputLevel] = useState(0);
    const stateRef = useRef<VoiceState>(state);
    stateRef.current = state;

    const streamRef = useRef<MediaStream | null>(null);
    const pcRef = useRef<RTCPeerConnection | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const contextRef = useRef<AudioContext | null>(null);
    const inputAnalyser = useRef<AnalyserNode | null>(null);
    const outputAnalyser = useRef<AnalyserNode | null>(null);
    const buffers = useRef<{ input: Float32Array<ArrayBuffer> | null; output: Float32Array<ArrayBuffer> | null }>({ input: null, output: null });
    const frame = useRef<number | null>(null);
    const timers = useRef<{ connect?: ReturnType<typeof setTimeout>; heartbeat?: ReturnType<typeof setInterval> }>({});
    const ending = useRef(false);
    const reconnecting = useRef(false);
    const turnTiming = useRef<{
        speaking: boolean;
        quietSince: number | null;
        speechEndAt: number | null;
        responseMs: number | null;
        botAudible: boolean;
        interruptAt: number | null;
        interruptionMs: number | null;
    }>({
        speaking: false,
        quietSince: null,
        speechEndAt: null,
        responseMs: null,
        botAudible: false,
        interruptAt: null,
        interruptionMs: null,
    });

    const stopMeters = useCallback(() => {
        if (frame.current !== null) cancelAnimationFrame(frame.current);
        frame.current = null;
        setInputLevel(0);
    }, []);

    /** Release everything: tracks, connection, socket, audio. */
    const release = useCallback(() => {
        clearTimeout(timers.current.connect);
        clearInterval(timers.current.heartbeat);
        timers.current = {};
        stopMeters();
        wsRef.current?.close();
        wsRef.current = null;
        pcRef.current?.getSenders().forEach((sender) => sender.track?.stop());
        pcRef.current?.close();
        pcRef.current = null;
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        if (audioRef.current) {
            audioRef.current.srcObject = null;
        }
        void contextRef.current?.close().catch(() => undefined);
        contextRef.current = null;
        inputAnalyser.current = null;
        outputAnalyser.current = null;
    }, [stopMeters]);

    const tick = useCallback(() => {
        const now = performance.now();
        const t = turnTiming.current;
        const muted = stateRef.current.muted;
        const input = muted ? 0 : level(inputAnalyser.current, buffers.current.input);
        const output = level(outputAnalyser.current, buffers.current.output);
        setInputLevel(input);
        // The person's speech: start above SPEAKING, end after SPEECH_END_MS below QUIET.
        if (input > SPEAKING) {
            if (!t.speaking && t.botAudible) t.interruptAt = now;
            t.speaking = true;
            t.quietSince = null;
        } else if (t.speaking && input < QUIET) {
            t.quietSince ??= now;
            if (now - t.quietSince >= SPEECH_END_MS) {
                t.speaking = false;
                t.speechEndAt = t.quietSince;
                t.responseMs = null;
            }
        }
        // Decibyl's playback, as heard.
        const audible = output > AUDIBLE;
        if (audible && !t.botAudible && t.speechEndAt !== null && t.responseMs === null) {
            t.responseMs = now - t.speechEndAt;
        }
        if (!audible && t.botAudible && t.interruptAt !== null && t.interruptionMs === null) {
            t.interruptionMs = now - t.interruptAt;
        }
        t.botAudible = audible;
        frame.current = requestAnimationFrame(tick);
    }, []);

    const startMeters = useCallback(
        (stream: MediaStream) => {
            try {
                const context = new AudioContext();
                contextRef.current = context;
                const analyser = context.createAnalyser();
                analyser.fftSize = 512;
                context.createMediaStreamSource(stream).connect(analyser);
                inputAnalyser.current = analyser;
                buffers.current.input = new Float32Array(analyser.fftSize);
                frame.current = requestAnimationFrame(tick);
            } catch {
                // No meter is honest: the screen shows the state label alone.
            }
        },
        [tick],
    );

    const watchPlayback = useCallback((remote: MediaStream) => {
        const context = contextRef.current;
        if (!context) return;
        try {
            const analyser = context.createAnalyser();
            analyser.fftSize = 512;
            context.createMediaStreamSource(remote).connect(analyser);
            outputAnalyser.current = analyser;
            buffers.current.output = new Float32Array(analyser.fftSize);
        } catch {
            // Playback still plays; only the measurement is missing.
        }
    }, []);

    const reportTurn = useCallback(
        async (turnIndex: number, interrupted: boolean) => {
            const sessionId = stateRef.current.sessionId;
            const t = turnTiming.current;
            const responseMs = t.responseMs;
            const interruptionMs = interrupted ? t.interruptionMs : null;
            t.interruptAt = null;
            t.interruptionMs = null;
            if (!measureLatency || sessionId === null) return;
            if (responseMs === null && interruptionMs === null) return;
            await recordTurnApiV1VoiceSessionsSessionIdTurnsPost({
                path: { session_id: sessionId },
                body: {
                    turn_index: turnIndex,
                    response_ms: responseMs === null ? null : Math.round(responseMs),
                    interruption_ms: interruptionMs === null ? null : Math.round(interruptionMs),
                    interrupted,
                },
            }).catch(() => undefined);
        },
        [measureLatency],
    );

    const end = useCallback(
        async (reason = "user_ended", notice?: string | null) => {
            ending.current = true;
            const sessionId = stateRef.current.sessionId;
            release();
            dispatch({ type: "ended", notice });
            if (sessionId !== null) {
                await endSessionApiV1VoiceSessionsSessionIdEndPost({
                    path: { session_id: sessionId },
                    body: { reason },
                }).catch(() => undefined);
            }
        },
        [release],
    );

    const fail = useCallback(
        async (reason: string, refusal: Refusal) => {
            ending.current = true;
            const sessionId = stateRef.current.sessionId;
            release();
            dispatch({ type: "refused", ...refusal });
            if (sessionId !== null) {
                await endSessionApiV1VoiceSessionsSessionIdEndPost({
                    path: { session_id: sessionId },
                    body: { reason },
                }).catch(() => undefined);
            }
        },
        [release],
    );

    // The current connect, for reconnect: set on every render below.
    const connectRef = useRef<(sessionId: number) => Promise<void>>(async () => undefined);

    const reconnect = useCallback(
        async (sessionId: number) => {
            if (ending.current || reconnecting.current) return;
            reconnecting.current = true;
            dispatch({ type: "connection_lost" });
            pcRef.current?.close();
            pcRef.current = null;
            wsRef.current?.close();
            wsRef.current = null;
            const current = await getSessionApiV1VoiceSessionsSessionIdGet({ path: { session_id: sessionId } }).catch(() => null);
            const session = current?.data;
            if (session && session.state === "live") {
                await moveSessionApiV1VoiceSessionsSessionIdMovePost({
                    path: { session_id: sessionId },
                    body: { expected_version: session.state_version, to: "reconnecting" },
                }).catch(() => undefined);
            }
            if (session && (session.state === "ended" || session.state === "failed")) {
                reconnecting.current = false;
                await end("lost", "This voice session has ended. Start again, or continue in text.");
                return;
            }
            for (const delay of RECONNECT_DELAYS_MS) {
                if (ending.current) break;
                await new Promise((resolve) => setTimeout(resolve, delay));
                if (ending.current) break;
                try {
                    await connectRef.current(sessionId);
                    reconnecting.current = false;
                    return;
                } catch {
                    // try the next delay
                }
            }
            reconnecting.current = false;
            if (!ending.current) {
                await end("lost", "Live voice could not reconnect. You can continue in text.");
            }
        },
        [end],
    );

    const onMessage = useCallback(
        async (raw: MessageEvent) => {
            let message: { type?: string; payload?: Record<string, unknown> };
            try {
                message = JSON.parse(String(raw.data));
            } catch {
                return;
            }
            const payload = message.payload ?? {};
            const pc = pcRef.current;
            switch (message.type) {
                case "answer":
                    if (pc) await pc.setRemoteDescription({ type: "answer", sdp: String(payload.sdp ?? "") });
                    return;
                case "ice-candidate":
                    if (pc && payload.candidate) {
                        const c = payload.candidate as { candidate: string; sdpMid?: string; sdpMLineIndex?: number };
                        await pc.addIceCandidate(c).catch(() => undefined);
                    }
                    return;
                case "error": {
                    const code = String(payload.error_type ?? "failed");
                    const text = String(payload.message ?? "Live voice stopped working. You can continue in text.");
                    await fail(code, { code, message: text });
                    return;
                }
                case "voice-live": {
                    clearTimeout(timers.current.connect);
                    const session = payload.session as { state_version?: number } | undefined;
                    dispatch({ type: "live", stateVersion: session?.state_version, gapSentence: (payload.gap_sentence as string | null) ?? null });
                    return;
                }
                case "voice-phase":
                    if (payload.phase === "processing") dispatch({ type: "processing" });
                    return;
                case "rtf-user-transcription":
                    dispatch({ type: "user_caption", text: String(payload.text ?? ""), final: Boolean(payload.final) });
                    return;
                case "rtf-bot-text":
                    dispatch({ type: "bot_caption", text: String(payload.text ?? "") });
                    return;
                case "rtf-bot-started-speaking":
                    dispatch({ type: "bot_started" });
                    return;
                case "rtf-bot-stopped-speaking":
                    dispatch({ type: "bot_stopped" });
                    return;
                case "voice-turn-closed":
                    dispatch({ type: "turn_closed", toolTurn: Boolean(payload.tool_turn) });
                    await reportTurn(Number(payload.turn_index ?? 0), Boolean(payload.interrupted));
                    return;
                case "call-ended": {
                    const sessionId = stateRef.current.sessionId;
                    if (!ending.current && sessionId !== null) void reconnect(sessionId);
                    return;
                }
                default:
                    return;
            }
        },
        [fail, reconnect, reportTurn],
    );

    const connect = async (sessionId: number) => {
        const stream = streamRef.current;
        if (!stream) throw new Error("no microphone");
        const token = await getAccessToken();
        const iceServers: RTCIceServer[] = [{ urls: ["stun:stun.l.google.com:19302"] }];
        try {
            const turn = await getTurnCredentialsApiV1TurnCredentialsGet();
            if (turn.data?.uris?.length) {
                iceServers.push({ urls: turn.data.uris, username: turn.data.username, credential: turn.data.password });
            }
        } catch {
            // STUN alone may still connect; a TURN outage is not fatal here.
        }
        const pc = new RTCPeerConnection({ iceServers });
        pcRef.current = pc;
        const pcId = newPcId();
        stream.getAudioTracks().forEach((track) => pc.addTrack(track, stream));
        pc.ontrack = (event) => {
            const [remote] = event.streams;
            if (!remote) return;
            audioRef.current ??= new Audio();
            audioRef.current.autoplay = true;
            audioRef.current.srcObject = remote;
            void audioRef.current.play().catch(() => undefined);
            watchPlayback(remote);
        };
        const base = (client.getConfig().baseUrl || resolveBrowserBackendUrl()).replace(/^http/, "ws");
        const ws = new WebSocket(`${base}/api/v1/ws/voice/${sessionId}`, token ? ["decibyl.auth", `bearer.${token}`] : []);
        wsRef.current = ws;
        pc.onicecandidate = (event) => {
            if (ws.readyState !== WebSocket.OPEN) return;
            ws.send(JSON.stringify({ type: "ice-candidate", payload: { candidate: event.candidate ? event.candidate.toJSON() : null, pc_id: pcId } }));
        };
        pc.onconnectionstatechange = () => {
            if (pcRef.current !== pc || ending.current) return;
            if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
                void reconnect(sessionId);
            }
        };
        ws.onmessage = (event) => void onMessage(event);
        await new Promise<void>((resolve, reject) => {
            ws.onopen = () => resolve();
            ws.onerror = () => reject(new Error("socket"));
        });
        ws.onclose = () => {
            if (wsRef.current === ws && !ending.current && ACTIVE.has(stateRef.current.phase)) void reconnect(sessionId);
        };
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        ws.send(JSON.stringify({ type: "offer", payload: { sdp: pc.localDescription?.sdp, type: "offer", pc_id: pcId } }));
    };
    connectRef.current = connect;

    const start = useCallback(
        async (context: StartContext) => {
            if (ACTIVE.has(stateRef.current.phase)) {
                dispatch({ type: "minimize", minimized: false });
                return;
            }
            ending.current = false;
            reconnecting.current = false;
            dispatch({ type: "open" });
            dispatch({ type: "mic_requested" });
            if (!navigator.mediaDevices?.getUserMedia) {
                dispatch({ type: "mic_missing" });
                return;
            }
            try {
                streamRef.current = await navigator.mediaDevices.getUserMedia({
                    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
                });
            } catch (error) {
                const name = (error as DOMException | undefined)?.name;
                dispatch({ type: name === "NotFoundError" || name === "OverconstrainedError" ? "mic_missing" : "mic_denied" });
                return;
            }
            const response = await startSessionApiV1VoiceSessionsPost({ body: { thread_id: context.threadId } });
            if (response.error || !response.data) {
                release();
                dispatch({ type: "refused", ...refusalOf(response.error, "Live voice could not start. You can continue in text.") });
                return;
            }
            const session = response.data;
            dispatch({ type: "session_started", sessionId: session.id, stateVersion: session.state_version });
            // The server started a new conversation (the start screen's
            // original was not this person's): Chat follows it there.
            if (session.thread_id && session.thread_id !== context.threadId) announceThreadStarted(session.thread_id);
            startMeters(streamRef.current);
            timers.current.connect = setTimeout(() => {
                if (stateRef.current.phase === "connecting") void fail("connect_timeout", { code: "connect_timeout", message: "The connection did not open. Try again, or continue in text." });
            }, CONNECT_TIMEOUT_MS);
            timers.current.heartbeat = setInterval(() => {
                void heartbeatApiV1VoiceSessionsSessionIdHeartbeatPost({ path: { session_id: session.id } }).catch(() => undefined);
            }, HEARTBEAT_MS);
            try {
                await connectRef.current(session.id);
            } catch {
                await fail("failed", { code: "unavailable", message: "Live voice could not connect. You can continue in text." });
            }
        },
        [fail, release, startMeters],
    );

    const toggleMute = useCallback(async () => {
        const muted = !stateRef.current.muted;
        streamRef.current?.getAudioTracks().forEach((track) => {
            track.enabled = !muted;
        });
        dispatch({ type: "muted", muted });
        const { sessionId, stateVersion } = stateRef.current;
        if (sessionId === null || stateVersion === null || state.phase === "reconnecting") return;
        const moved = await moveSessionApiV1VoiceSessionsSessionIdMovePost({
            path: { session_id: sessionId },
            body: { expected_version: stateVersion, to: "live", muted },
        }).catch(() => null);
        if (moved?.data) dispatch({ type: "muted", muted, stateVersion: moved.data.state_version });
    }, [state.phase]);

    const minimize = useCallback((minimized: boolean) => dispatch({ type: "minimize", minimized }), []);
    const close = useCallback(() => {
        release();
        dispatch({ type: "reset" });
    }, [release]);

    // Leaving the page ends the session: nothing keeps listening unseen.
    useEffect(() => {
        const leave = () => {
            const sessionId = stateRef.current.sessionId;
            if (sessionId !== null && ACTIVE.has(stateRef.current.phase)) {
                // The heartbeat stops with the page, and the server sweeps
                // the session as lost; the microphone is released now.
                ending.current = true;
                release();
            }
        };
        window.addEventListener("pagehide", leave);
        return () => {
            window.removeEventListener("pagehide", leave);
            release();
        };
    }, [release]);

    return { state, inputLevel, start, end, toggleMute, minimize, close };
}

export type LiveVoice = ReturnType<typeof useLiveVoice>;
