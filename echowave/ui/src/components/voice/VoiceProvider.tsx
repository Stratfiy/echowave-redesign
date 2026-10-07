"use client";

/**
 * Fills the shell's Talk entry point (lib/shell/chatEntryPoints.ts) with
 * live voice, while `decibyl_voice` is on. Off, nothing registers and Chat
 * keeps its honest "Live voice is not available yet" -- exactly today's
 * behaviour. Mounted once beside the app layout so a session survives moving
 * between Chat and Today, with the strip keeping it one tap away.
 */

import { useCallback, useEffect, useRef } from "react";

import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { type EntryContext, registerTalk } from "@/lib/shell/chatEntryPoints";
import { useLiveVoice } from "@/lib/voice/useLiveVoice";

import { LiveVoiceSheet } from "./LiveVoiceSheet";

export function VoiceProvider() {
    const on = useFeature("decibyl_voice");
    const measureLatency = useFeature("voice_latency");
    const { getAccessToken } = useAuth();
    const voice = useLiveVoice({ getAccessToken, measureLatency });
    const last = useRef<EntryContext>({ threadId: null, draft: "" });
    const { start } = voice;

    useEffect(() => {
        if (!on) return;
        return registerTalk((context) => {
            last.current = context;
            void start(context);
        });
    }, [on, start]);

    const retry = useCallback(() => void start(last.current), [start]);

    if (!on) return null;
    return (
        <LiveVoiceSheet
            state={voice.state}
            inputLevel={voice.inputLevel}
            onEnd={() => void voice.end()}
            onToggleMute={() => void voice.toggleMute()}
            onMinimize={voice.minimize}
            onRetry={retry}
            onClose={voice.close}
        />
    );
}
