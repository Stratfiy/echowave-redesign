"use client";

/**
 * A huddle with one agent: the live voice hook (lib/voice/useLiveVoice)
 * pointed at the huddle's own routes and socket (api/routes/huddle.py,
 * /ws/huddle). Microphone, WebRTC, reconnect, captions and End are Talk's;
 * what differs is who answers -- the agent, as a teammate -- and that a
 * change it proposes lands as a card in the agent's thread, which the page
 * is told about so the thread shows it at once.
 *
 * Held by the thread page rather than the panel, so putting the panel away
 * (to read the card on a phone) does not end the conversation.
 */

import { useMemo } from "react";

import {
    endHuddleApiV1HuddleSessionsSessionIdEndPost,
    getHuddleApiV1HuddleSessionsSessionIdGet,
    heartbeatHuddleApiV1HuddleSessionsSessionIdHeartbeatPost,
    moveHuddleApiV1HuddleSessionsSessionIdMovePost,
    startHuddleApiV1HuddleWorkflowIdSessionsPost,
} from "@/client/sdk.gen";
import { useAuth } from "@/lib/auth";
import { useLiveVoice, type VoiceSessionApi } from "@/lib/voice/useLiveVoice";

export function huddleApi(workflowId: number): VoiceSessionApi {
    return {
        start: () => startHuddleApiV1HuddleWorkflowIdSessionsPost({ path: { workflow_id: workflowId } }),
        get: (sessionId) => getHuddleApiV1HuddleSessionsSessionIdGet({ path: { session_id: sessionId } }),
        move: (sessionId, body) => moveHuddleApiV1HuddleSessionsSessionIdMovePost({ path: { session_id: sessionId }, body }),
        heartbeat: (sessionId) =>
            heartbeatHuddleApiV1HuddleSessionsSessionIdHeartbeatPost({ path: { session_id: sessionId } }),
        end: (sessionId, reason) =>
            endHuddleApiV1HuddleSessionsSessionIdEndPost({ path: { session_id: sessionId }, body: { reason } }),
        socketPath: (sessionId) => `/api/v1/ws/huddle/${sessionId}`,
    };
}

export function useHuddle({
    workflowId,
    onCard,
    onNote,
}: {
    workflowId: number;
    /** A change was proposed: its card is on the thread now. */
    onCard?: (eventId: number) => void;
    /** The agent kept a note for next time. */
    onNote?: () => void;
}) {
    const { getAccessToken } = useAuth();
    const api = useMemo(() => huddleApi(workflowId), [workflowId]);
    return useLiveVoice({
        getAccessToken,
        // Latency is measured on Talk; a huddle's turns are the same pipeline.
        measureLatency: false,
        api,
        onServerEvent: (type, payload) => {
            if (type === "huddle-card") onCard?.(Number(payload.event_id ?? 0));
            if (type === "huddle-note") onNote?.();
        },
    });
}

export type Huddle = ReturnType<typeof useHuddle>;
