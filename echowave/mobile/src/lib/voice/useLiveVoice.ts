/**
 * Live voice with Decibyl on the phone (screen 05; handoff 7 mobile, 12, 21).
 * Distinct from dictation: this is a conversation, not words into the box.
 *
 * The same contract as the web hook (ui/src/lib/voice/useLiveVoice.ts):
 * microphone first, so a denial is said before anything starts; then a
 * session (POST /voice/sessions, which refuses honestly when voice needs
 * setup or the day's minutes are spent); then WebRTC over the voice
 * WebSocket (/api/v1/ws/voice/{id}, bearer token in the WebSocket protocol
 * list), with ICE servers from GET /turn/credentials. The server's caption
 * and turn messages drive the shared state machine.
 *
 * - Mute stops the track and is recorded on the session.
 * - End always works: it ends the session, closes the connection and stops
 *   the microphone.
 * - A dropped connection reconnects on the same session with backoff;
 *   nothing said is replayed, so no task is duplicated.
 * - Approvals are never taken by voice (VOICE.md): a turn that proposed a
 *   card says the card waits in Chat.
 *
 * Not ported from the web: per-turn latency measurement, which needs the
 * Web Audio analyser the phone does not have (flag `voice_latency` stays a
 * web measurement for now).
 */
import { useCallback, useEffect, useLayoutEffect, useReducer, useRef } from 'react';

import { client } from '@/client/client.gen';
import {
    endSessionApiV1VoiceSessionsSessionIdEndPost,
    getSessionApiV1VoiceSessionsSessionIdGet,
    getTurnCredentialsApiV1TurnCredentialsGet,
    heartbeatApiV1VoiceSessionsSessionIdHeartbeatPost,
    moveSessionApiV1VoiceSessionsSessionIdMovePost,
    startSessionApiV1VoiceSessionsPost,
} from '@/client/sdk.gen';
import { getToken } from '@/lib/auth/token';

import { mediaDevices, playRemote, RTCPeerConnection, stopRemote } from './rtc';
import { ACTIVE, INITIAL, reduce, type VoiceState } from './sessionState';

export const CONNECT_TIMEOUT_MS = 15_000;
export const HEARTBEAT_MS = 20_000;
export const RECONNECT_DELAYS_MS = [1_000, 2_000, 4_000];

type Refusal = { code: string; message: string };

/** The parts of a peer connection used here, common to both platforms. */
type Track = { enabled: boolean; stop(): void };
type Stream = { getTracks(): Track[]; getAudioTracks(): Track[] };
type Peer = {
    addTrack(track: Track, stream: Stream): void;
    createOffer(options?: object): Promise<{ sdp?: string; type?: string }>;
    setLocalDescription(description: { sdp?: string; type?: string }): Promise<void>;
    setRemoteDescription(description: { sdp: string; type: 'answer' }): Promise<void>;
    addIceCandidate(candidate: object): Promise<void>;
    close(): void;
    connectionState: string;
    localDescription: { sdp?: string } | null;
    addEventListener(type: string, listener: (event: never) => void): void;
};

export function refusalOf(error: unknown, fallback: string): Refusal {
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        const d = detail as { code?: string; message?: string; next_step?: string };
        return { code: d.code ?? 'failed', message: [d.message, d.next_step].filter(Boolean).join(' ') || fallback };
    }
    if (typeof detail === 'string') return { code: 'failed', message: detail };
    return { code: 'failed', message: fallback };
}

function newPcId(): string {
    let hex = '';
    for (let i = 0; i < 32; i += 1) hex += Math.floor(Math.random() * 16).toString(16);
    return `PC-${hex}`;
}

export function voiceSocketUrl(baseUrl: string, sessionId: number): string {
    return `${baseUrl.replace(/\/+$/, '').replace(/^http/, 'ws')}/api/v1/ws/voice/${sessionId}`;
}

export function useLiveVoice() {
    const [state, dispatch] = useReducer(reduce, INITIAL);
    const stateRef = useRef<VoiceState>(state);
    useLayoutEffect(() => {
        stateRef.current = state;
    }, [state]);

    const streamRef = useRef<Stream | null>(null);
    const pcRef = useRef<Peer | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const timers = useRef<{ connect?: ReturnType<typeof setTimeout>; heartbeat?: ReturnType<typeof setInterval> }>({});
    const ending = useRef(false);
    const reconnecting = useRef(false);

    const release = useCallback(() => {
        clearTimeout(timers.current.connect);
        clearInterval(timers.current.heartbeat);
        timers.current = {};
        wsRef.current?.close();
        wsRef.current = null;
        pcRef.current?.close();
        pcRef.current = null;
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        stopRemote();
    }, []);

    const endOnServer = async (reason: string) => {
        const sessionId = stateRef.current.sessionId;
        if (sessionId === null) return;
        await endSessionApiV1VoiceSessionsSessionIdEndPost({ path: { session_id: sessionId }, body: { reason } }).catch(
            () => undefined,
        );
    };

    const end = useCallback(
        async (reason = 'user_ended', notice?: string | null) => {
            ending.current = true;
            release();
            dispatch({ type: 'ended', notice });
            await endOnServer(reason);
        },
        [release],
    );

    const fail = useCallback(
        async (reason: string, refusal: Refusal) => {
            ending.current = true;
            release();
            dispatch({ type: 'refused', ...refusal });
            await endOnServer(reason);
        },
        [release],
    );

    const connectRef = useRef<(sessionId: number) => Promise<void>>(async () => undefined);

    const reconnect = useCallback(
        async (sessionId: number) => {
            if (ending.current || reconnecting.current) return;
            reconnecting.current = true;
            dispatch({ type: 'connection_lost' });
            pcRef.current?.close();
            pcRef.current = null;
            wsRef.current?.close();
            wsRef.current = null;
            const current = await getSessionApiV1VoiceSessionsSessionIdGet({ path: { session_id: sessionId } }).catch(
                () => null,
            );
            const session = current?.data;
            if (session && session.state === 'live') {
                await moveSessionApiV1VoiceSessionsSessionIdMovePost({
                    path: { session_id: sessionId },
                    body: { expected_version: session.state_version, to: 'reconnecting' },
                }).catch(() => undefined);
            }
            if (session && (session.state === 'ended' || session.state === 'failed')) {
                reconnecting.current = false;
                await end('lost', 'This voice session has ended. Start again, or continue in text.');
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
                    // The next delay.
                }
            }
            reconnecting.current = false;
            if (!ending.current) await end('lost', 'Live voice could not reconnect. You can continue in text.');
        },
        [end],
    );

    const onMessage = useCallback(
        async (raw: { data: unknown }) => {
            let message: { type?: string; payload?: Record<string, unknown> };
            try {
                message = JSON.parse(String(raw.data));
            } catch {
                return;
            }
            const payload = message.payload ?? {};
            const pc = pcRef.current;
            switch (message.type) {
                case 'answer':
                    if (pc) await pc.setRemoteDescription({ type: 'answer', sdp: String(payload.sdp ?? '') });
                    return;
                case 'ice-candidate':
                    if (pc && payload.candidate) await pc.addIceCandidate(payload.candidate as object).catch(() => undefined);
                    return;
                case 'error': {
                    const code = String(payload.error_type ?? 'failed');
                    await fail(code, {
                        code,
                        message: String(payload.message ?? 'Live voice stopped working. You can continue in text.'),
                    });
                    return;
                }
                case 'voice-live': {
                    clearTimeout(timers.current.connect);
                    const session = payload.session as { state_version?: number } | undefined;
                    dispatch({
                        type: 'live',
                        stateVersion: session?.state_version,
                        gapSentence: (payload.gap_sentence as string | null) ?? null,
                    });
                    return;
                }
                case 'voice-phase':
                    if (payload.phase === 'processing') dispatch({ type: 'processing' });
                    return;
                case 'rtf-user-transcription':
                    dispatch({ type: 'user_caption', text: String(payload.text ?? ''), final: Boolean(payload.final) });
                    return;
                case 'rtf-bot-text':
                    dispatch({ type: 'bot_caption', text: String(payload.text ?? '') });
                    return;
                case 'rtf-bot-started-speaking':
                    dispatch({ type: 'bot_started' });
                    return;
                case 'rtf-bot-stopped-speaking':
                    dispatch({ type: 'bot_stopped' });
                    return;
                case 'voice-turn-closed':
                    dispatch({ type: 'turn_closed', toolTurn: Boolean(payload.tool_turn) });
                    return;
                case 'call-ended': {
                    const sessionId = stateRef.current.sessionId;
                    if (!ending.current && sessionId !== null) void reconnect(sessionId);
                    return;
                }
                default:
                    return;
            }
        },
        [fail, reconnect],
    );

    const connect = async (sessionId: number) => {
        const stream = streamRef.current;
        if (!stream) throw new Error('no microphone');
        const token = getToken();
        const iceServers: { urls: string[]; username?: string; credential?: string }[] = [
            { urls: ['stun:stun.l.google.com:19302'] },
        ];
        try {
            const turn = await getTurnCredentialsApiV1TurnCredentialsGet();
            if (turn.data?.uris?.length) {
                iceServers.push({ urls: turn.data.uris, username: turn.data.username, credential: turn.data.password });
            }
        } catch {
            // STUN alone may still connect.
        }
        const pc = new (RTCPeerConnection as unknown as new (config: object) => Peer)({ iceServers });
        pcRef.current = pc;
        const pcId = newPcId();
        stream.getAudioTracks().forEach((track) => pc.addTrack(track, stream));
        pc.addEventListener('track', (event: { streams?: unknown[] }) => {
            const remote = event.streams?.[0];
            if (remote) playRemote(remote as never);
        });
        const base = client.getConfig().baseUrl || '';
        const ws = new WebSocket(voiceSocketUrl(base, sessionId), token ? ['decibyl.auth', `bearer.${token}`] : []);
        wsRef.current = ws;
        pc.addEventListener('icecandidate', (event: { candidate?: { toJSON?: () => object } | null }) => {
            if (ws.readyState !== WebSocket.OPEN) return;
            const candidate = event.candidate ? (event.candidate.toJSON?.() ?? event.candidate) : null;
            ws.send(JSON.stringify({ type: 'ice-candidate', payload: { candidate, pc_id: pcId } }));
        });
        pc.addEventListener('connectionstatechange', () => {
            if (pcRef.current !== pc || ending.current) return;
            if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') void reconnect(sessionId);
        });
        ws.onmessage = (event) => void onMessage(event);
        await new Promise<void>((resolve, reject) => {
            ws.onopen = () => resolve();
            ws.onerror = () => reject(new Error('socket'));
        });
        ws.onclose = () => {
            if (wsRef.current === ws && !ending.current && ACTIVE.has(stateRef.current.phase)) void reconnect(sessionId);
        };
        const offer = await pc.createOffer({});
        await pc.setLocalDescription(offer);
        ws.send(JSON.stringify({ type: 'offer', payload: { sdp: pc.localDescription?.sdp, type: 'offer', pc_id: pcId } }));
    };
    useEffect(() => {
        connectRef.current = connect;
    });

    const start = useCallback(
        async (threadId: string | null) => {
            if (ACTIVE.has(stateRef.current.phase)) return;
            ending.current = false;
            reconnecting.current = false;
            dispatch({ type: 'open' });
            dispatch({ type: 'mic_requested' });
            if (!mediaDevices?.getUserMedia) {
                dispatch({ type: 'mic_missing' });
                return;
            }
            try {
                streamRef.current = (await mediaDevices.getUserMedia({
                    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } as never,
                    video: false,
                })) as unknown as Stream;
            } catch (error) {
                const name = (error as { name?: string } | undefined)?.name;
                dispatch({ type: name === 'NotFoundError' || name === 'OverconstrainedError' ? 'mic_missing' : 'mic_denied' });
                return;
            }
            const response = await startSessionApiV1VoiceSessionsPost({ body: { thread_id: threadId } });
            if (response.error || !response.data) {
                release();
                dispatch({
                    type: 'refused',
                    ...refusalOf(response.error, 'Live voice could not start. You can continue in text.'),
                });
                return;
            }
            const session = response.data;
            dispatch({ type: 'session_started', sessionId: session.id, stateVersion: session.state_version });
            timers.current.connect = setTimeout(() => {
                if (stateRef.current.phase === 'connecting') {
                    void fail('connect_timeout', {
                        code: 'connect_timeout',
                        message: 'The connection did not open. Try again, or continue in text.',
                    });
                }
            }, CONNECT_TIMEOUT_MS);
            timers.current.heartbeat = setInterval(() => {
                void heartbeatApiV1VoiceSessionsSessionIdHeartbeatPost({ path: { session_id: session.id } }).catch(
                    () => undefined,
                );
            }, HEARTBEAT_MS);
            try {
                await connectRef.current(session.id);
            } catch {
                await fail('failed', { code: 'unavailable', message: 'Live voice could not connect. You can continue in text.' });
            }
        },
        [fail, release],
    );

    const toggleMute = useCallback(async () => {
        const muted = !stateRef.current.muted;
        streamRef.current?.getAudioTracks().forEach((track) => {
            track.enabled = !muted;
        });
        dispatch({ type: 'muted', muted });
        const { sessionId, stateVersion, phase } = stateRef.current;
        if (sessionId === null || stateVersion === null || phase === 'reconnecting') return;
        const moved = await moveSessionApiV1VoiceSessionsSessionIdMovePost({
            path: { session_id: sessionId },
            body: { expected_version: stateVersion, to: 'live', muted },
        }).catch(() => null);
        if (moved?.data) dispatch({ type: 'muted', muted, stateVersion: moved.data.state_version });
    }, []);

    const reset = useCallback(() => {
        release();
        dispatch({ type: 'reset' });
    }, [release]);

    // Leaving the screen ends the session: nothing keeps listening unseen.
    useEffect(
        () => () => {
            if (ACTIVE.has(stateRef.current.phase)) {
                ending.current = true;
                void endOnServer('user_left');
            }
            release();
        },
        [release],
    );

    return { state, start, end, toggleMute, reset };
}
