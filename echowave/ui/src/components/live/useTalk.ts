'use client';

/**
 * Speaking on a live call from the browser: the microphone and the talking
 * socket (`/ws/live-calls/{run_id}/talk`).
 *
 * In three explicit steps, so nothing is half done: `openMic` asks for the
 * microphone (before joining, so a refusal never leaves a call joined with
 * nobody to speak), the caller joins the call through the API, then
 * `connect` opens the socket and starts sending. `stop` undoes all of it.
 * While the socket is open the server tells the call the supervisor is still
 * there; when it closes for any reason the agent takes the call back after a
 * few seconds, so the caller is never left with silence. A supervisor on the
 * call by phone opens it too, with no microphone (`presenceOnly`): the page
 * staying open is what says they are still there.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { client } from '@/client/client.gen';
import { resolveBrowserBackendUrl } from '@/lib/apiClient';
import { useAuth } from '@/lib/auth';

import { downsample, encodeMic, levelOf, MIC_RATE, Microphone } from './talkAudio';

export type TalkStatus = 'idle' | 'connecting' | 'live' | 'released' | 'ended' | 'error';

export function talkUrl(runId: number): string {
    const base = (client.getConfig().baseUrl || resolveBrowserBackendUrl()).replace(/^http/, 'ws');
    return `${base}/api/v1/ws/live-calls/${runId}/talk`;
}

export function useTalk(runId: number) {
    const { getAccessToken } = useAuth();
    const [status, setStatus] = useState<TalkStatus>('idle');
    const [error, setError] = useState<string | null>(null);
    const [muted, setMutedState] = useState(false);
    const [level, setLevel] = useState(0);
    const mic = useRef<Microphone | null>(null);
    const socket = useRef<WebSocket | null>(null);
    const mutedRef = useRef(false);
    const lastLevel = useRef(0);

    const stop = useCallback(() => {
        mic.current?.close();
        mic.current = null;
        const ws = socket.current;
        socket.current = null;
        if (ws) {
            ws.onclose = null;
            ws.close();
        }
        setLevel(0);
    }, []);

    const openMic = useCallback(async (): Promise<boolean> => {
        setError(null);
        if (mic.current) return true;
        try {
            mic.current = await Microphone.open();
            return true;
        } catch {
            setError('Allow the microphone to speak on the call.');
            return false;
        }
    }, []);

    const connect = useCallback(async ({ presenceOnly = false }: { presenceOnly?: boolean } = {}) => {
        if (!mic.current && !presenceOnly) return;
        setStatus('connecting');
        const token = await getAccessToken();
        const ws = new WebSocket(talkUrl(runId), token ? ['decibyl.auth', `bearer.${token}`] : []);
        ws.binaryType = 'arraybuffer';
        socket.current = ws;
        ws.onmessage = (message) => {
            if (typeof message.data !== 'string') return;
            let event: { type?: string; detail?: string };
            try {
                event = JSON.parse(message.data);
            } catch {
                return;
            }
            if (event.type === 'live') setStatus('live');
            else if (event.type === 'released' || event.type === 'ended') {
                setStatus(event.type);
                stop();
            } else if (event.type === 'error') {
                setError(event.detail || 'Could not put your microphone on the call.');
                setStatus('error');
                stop();
            }
        };
        ws.onclose = () => {
            if (socket.current !== ws) return;
            setStatus((was) => (was === 'live' || was === 'connecting' ? 'error' : was));
            setError((was) => was ?? 'Lost the connection. The agent takes the call back in a few seconds.');
            stop();
        };
        if (!mic.current) return;
        mic.current.onSamples = (samples, rate) => {
            const now = Date.now();
            if (now - lastLevel.current > 120) {
                lastLevel.current = now;
                setLevel(mutedRef.current ? 0 : levelOf(samples));
            }
            if (mutedRef.current || ws.readyState !== WebSocket.OPEN) return;
            ws.send(encodeMic(downsample(samples, rate, MIC_RATE), MIC_RATE));
        };
    }, [getAccessToken, runId, stop]);

    const setMuted = useCallback((value: boolean) => {
        mutedRef.current = value;
        setMutedState(value);
    }, []);

    const reset = useCallback(() => {
        stop();
        setStatus('idle');
        setError(null);
        setMuted(false);
    }, [stop, setMuted]);

    useEffect(() => stop, [stop]);

    return { status, error, muted, level, openMic, connect, stop, reset, setMuted };
}
