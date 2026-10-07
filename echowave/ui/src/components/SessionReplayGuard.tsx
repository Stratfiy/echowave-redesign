'use client';

import { usePathname } from 'next/navigation';
import posthog from 'posthog-js';
import { useEffect } from 'react';

import { useFeature } from '@/lib/features';
import { isReplayAllowed } from '@/lib/telemetry/privacy';

/**
 * Starts PostHog session replay only where it is allowed, and stops it the
 * moment the person moves to any other screen (handoff 35). PostHog is
 * initialised with replay disabled (instrumentation-client.ts), so with the
 * `session_replay` flag off nothing is ever recorded.
 */
export default function SessionReplayGuard() {
    const pathname = usePathname();
    const enabled = useFeature('session_replay');

    useEffect(() => {
        if (!posthog.__loaded) return;
        try {
            if (enabled && isReplayAllowed(pathname)) {
                posthog.startSessionRecording();
            } else if (posthog.sessionRecordingStarted()) {
                posthog.stopSessionRecording();
            }
        } catch (err) {
            console.warn('Session replay guard failed', err);
        }
    }, [enabled, pathname]);

    return null;
}
