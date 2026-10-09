'use client';

/**
 * The calls in progress, refreshed while the screen is open. Nothing is
 * fetched while `live_supervision` is off: the strip has nothing to draw.
 */

import { useCallback, useEffect, useState } from 'react';

import { listLiveCallsApiV1LiveCallsGet } from '@/client/sdk.gen';
import type { LiveCallsResponse } from '@/client/types.gen';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { useFeature } from '@/lib/features';

/** How often the list is asked again. A call's own panel streams; this is
 *  only for calls starting and ending. */
export const REFRESH_MS = 5000;

export function useLiveCalls(workflowId?: number) {
    const on = useFeature('live_supervision');
    const { user, loading: authLoading } = useAuth();
    const signedIn = Boolean(user);
    const [data, setData] = useState<LiveCallsResponse | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const result = await listLiveCallsApiV1LiveCallsGet({
            query: workflowId !== undefined ? { workflow_id: workflowId } : {},
        });
        if (result.error) {
            setError(detailFromError(result.error, 'Live calls could not load.'));
            return;
        }
        setError(null);
        setData(result.data ?? null);
    }, [workflowId]);

    useEffect(() => {
        if (!on || authLoading || !signedIn) return;
        void load();
        const timer = window.setInterval(() => {
            if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
            void load();
        }, REFRESH_MS);
        return () => window.clearInterval(timer);
    }, [on, authLoading, signedIn, load]);

    return { on, data, error, reload: load, setData };
}
