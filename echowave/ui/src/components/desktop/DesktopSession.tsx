'use client';

/**
 * Tells the desktop app who is signed in, so "work on my computer" can put
 * its approval cards and receipt on this person's thread. Renders nothing,
 * and does nothing in a browser or while both desktop switches are off.
 *
 * The token is handed over in memory and refreshed every few minutes; the
 * desktop never writes it to disk. Signing out clears it.
 */

import { useEffect } from 'react';

import { client } from '@/client/client.gen';
import { useAuth } from '@/lib/auth';
import { currentThread, desktopBridge } from '@/lib/desktop';
import { useFeature } from '@/lib/features';

const REFRESH_MS = 4 * 60 * 1000;

export function DesktopSession() {
    const { user, loading, getAccessToken } = useAuth();
    const app = useFeature('desktop_app');
    const computerUse = useFeature('desktop_computer_use');
    const on = app || computerUse;

    useEffect(() => {
        const bridge = desktopBridge();
        if (!bridge || loading || !on) return;
        if (!user) {
            void bridge.setSession(null).catch(() => undefined);
            return;
        }
        let cancelled = false;
        const send = async () => {
            try {
                const token = await getAccessToken();
                const apiBase = client.getConfig().baseUrl || window.location.origin;
                if (!cancelled && token) await bridge.setSession({ apiBase, token, threadId: currentThread() });
            } catch {
                // The desktop keeps the last session; a missed refresh only
                // means a card may need the person to open the app again.
            }
        };
        void send();
        const timer = window.setInterval(() => void send(), REFRESH_MS);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, [user, loading, on, getAccessToken]);

    return null;
}
