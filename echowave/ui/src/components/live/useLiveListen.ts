'use client';

/**
 * The listen socket for one call: its words as they are said, the whispers,
 * and -- when asked for -- its sound. Receive-only: nothing is sent up it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { client } from '@/client/client.gen';
import { resolveBrowserBackendUrl } from '@/lib/apiClient';
import { useAuth } from '@/lib/auth';

import { LivePlayer, parsePacket } from './liveAudio';
import { apply, applyAll, EMPTY, type LiveEvent, type TranscriptState } from './transcript';

export type ListenStatus = 'connecting' | 'open' | 'ended' | 'error';

export function listenUrl(runId: number, audio: boolean): string {
    const base = (client.getConfig().baseUrl || resolveBrowserBackendUrl()).replace(/^http/, 'ws');
    return `${base}/api/v1/ws/live-calls/${runId}${audio ? '?audio=1' : ''}`;
}

export function useLiveListen(runId: number, { audio, initial }: { audio: boolean; initial?: LiveEvent[] }) {
    const { user, loading: authLoading, getAccessToken } = useAuth();
    const [state, setState] = useState<TranscriptState>(() => applyAll(EMPTY, initial ?? []));
    const [status, setStatus] = useState<ListenStatus>('connecting');
    const [error, setError] = useState<string | null>(null);
    const player = useRef<LivePlayer | null>(null);

    const onEvent = useCallback((event: LiveEvent) => {
        if (event.type === 'hello') {
            const backlog = (event as unknown as { transcript?: LiveEvent[] }).transcript ?? [];
            setState((was) => applyAll(was, backlog));
            setStatus('open');
            return;
        }
        if (event.type === 'error') {
            setError(event.detail || 'Could not listen to this call.');
            setStatus('error');
            return;
        }
        if (event.type === 'interrupted') player.current?.interrupt();
        if (event.type === 'ended') setStatus('ended');
        setState((was) => apply(was, event));
    }, []);

    useEffect(() => {
        if (authLoading || !user) return;
        let closed = false;
        let ws: WebSocket | null = null;
        if (audio) {
            try {
                player.current = new LivePlayer();
                void player.current.resume();
            } catch {
                player.current = null;
                setError('This browser cannot play the call’s audio. The words still come through.');
            }
        }
        void (async () => {
            const token = await getAccessToken();
            if (closed) return;
            ws = new WebSocket(listenUrl(runId, audio), token ? ['decibyl.auth', `bearer.${token}`] : []);
            ws.binaryType = 'arraybuffer';
            ws.onmessage = (message) => {
                if (typeof message.data === 'string') {
                    try {
                        onEvent(JSON.parse(message.data) as LiveEvent);
                    } catch {
                        // Not ours to read.
                    }
                    return;
                }
                const packet = parsePacket(message.data as ArrayBuffer);
                if (packet) player.current?.play(packet);
            };
            ws.onerror = () => {
                if (!closed) setStatus((was) => (was === 'ended' ? was : 'error'));
            };
            ws.onclose = () => {
                if (!closed) setStatus((was) => (was === 'open' || was === 'connecting' ? 'ended' : was));
            };
        })();
        return () => {
            closed = true;
            ws?.close();
            void player.current?.close();
            player.current = null;
        };
    }, [authLoading, user, getAccessToken, runId, audio, onEvent]);

    return { state, status, error };
}
