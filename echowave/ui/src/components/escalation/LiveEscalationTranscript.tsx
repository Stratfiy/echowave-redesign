'use client';

/**
 * The handoff card's "Live transcript" link lands here: the call's run page
 * opened with `?live=1&escalation=<uuid>` while the call is still going.
 *
 * A finished run has its full transcript on the run page; a live one does
 * not yet. So while it lasts this shows where the handover stands and the
 * last lines of the call, which the escalation runtime keeps current on the
 * card (`live_transcript`), refreshed every few seconds. When the run ends,
 * `onRunEnded` lets the page swap to the full details.
 *
 * Only reachable with escalation_v2 on; the page ignores the flag in the URL
 * otherwise.
 */

import { Radio } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { getEscalationApiV1EscalationsEscalationUuidGet } from '@/client/sdk.gen';
import type { EscalationResponse } from '@/client/types.gen';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

import { stateLine } from './HandoffCard';

export const LIVE_REFRESH_MS = 3000;

type LiveCard = {
    summary?: string;
    reason?: string;
    team?: string | null;
    live_transcript?: string[];
};

export function LiveEscalationTranscript({
    escalationUuid,
    isRunLive,
    refreshMs = LIVE_REFRESH_MS,
}: {
    escalationUuid: string;
    /** Asked on each tick; false once the run has ended. */
    isRunLive: () => Promise<boolean>;
    refreshMs?: number;
}) {
    const { user, loading: authLoading } = useAuth();
    const [data, setData] = useState<EscalationResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const stopped = useRef(false);

    useEffect(() => {
        if (authLoading || !user) return;
        stopped.current = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const tick = async () => {
            const res = await getEscalationApiV1EscalationsEscalationUuidGet({ path: { escalation_uuid: escalationUuid } });
            if (stopped.current) return;
            if (res.error) {
                setError(detailFromError(res.error, 'Could not load this call.'));
            } else if (res.data) {
                setError(null);
                setData(res.data);
            }
            if (!(await isRunLive()) || stopped.current) return;
            timer = setTimeout(() => void tick(), refreshMs);
        };
        void tick();
        return () => {
            stopped.current = true;
            if (timer) clearTimeout(timer);
        };
    }, [authLoading, user, escalationUuid, isRunLive, refreshMs]);

    const card = (data?.handoff_card ?? {}) as LiveCard;
    const lines = card.live_transcript ?? [];

    return (
        <section className="mx-auto w-full max-w-2xl space-y-3 p-6" aria-label="Live call" data-testid="live-escalation">
            <p className="flex items-center gap-2 text-sm font-medium">
                <Radio className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                This call is live
            </p>
            {data && (
                <p className="text-xs text-muted-foreground" data-testid="live-escalation-state">
                    {card.reason ? `${card.reason} · ` : ''}
                    {card.team ? `${card.team} · ` : ''}
                    {stateLine(data.state, data.failure_reason, data.fallback)}
                </p>
            )}
            {card.summary && <p className="text-sm">{card.summary}</p>}
            <ol className="space-y-1 rounded-md border border-border p-3 text-sm" aria-label="Latest lines">
                {lines.length === 0 ? (
                    <li className="text-muted-foreground">Nothing said yet.</li>
                ) : (
                    lines.map((line, i) => (
                        <li key={i} className="break-words">
                            {line}
                        </li>
                    ))
                )}
            </ol>
            <p className="text-xs text-muted-foreground">
                Refreshes every few seconds. The full transcript appears here when the call ends.
            </p>
            {error && (
                <p className="text-sm text-red-700 dark:text-red-300" role="alert">
                    {error}
                </p>
            )}
        </section>
    );
}

export default LiveEscalationTranscript;
