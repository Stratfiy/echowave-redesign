'use client';

/**
 * How a bot is standing right now: whether it is working, what it did today,
 * what is broken, and which channel it belongs to.
 *
 * Buzz's agent profile leads with Status, Runtime and Last error before any
 * of the configuration. The reasoning is its own rule -- failures rise, reads
 * recede -- and ours had the opposite shape: the panel opened on Instructions
 * and a bot whose Gmail had been failing all morning looked identical to one
 * having a perfect day.
 *
 * What is broken is one line here, not a copy of the readiness card on the
 * settings page. The line rises; the detail stays where it already lives.
 */

import { AlertTriangle, CircleDot, Hash } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

import {
    agentReadinessApiV1WorkflowWorkflowIdReadinessGet,
    listFoldersApiV1FolderGet,
    teamStatusApiV1TeamStatusGet,
} from '@/client/sdk.gen';
import type { ReadinessItem, TeamMember } from '@/client/types.gen';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

/** The dot beside the state, in the tones the rail already uses. */
const TONE_DOT: Record<string, string> = {
    good: 'bg-emerald-500',
    busy: 'bg-emerald-500',
    failing: 'bg-[var(--destructive)]',
    paused: 'bg-amber-500',
    idle: 'bg-muted-foreground/40',
};

/** What to call the state, in words rather than a status code. */
export function standing(member: TeamMember | null): { label: string; tone: string } {
    if (!member) return { label: 'Not started yet', tone: 'idle' };
    if (!member.is_live) return { label: 'Paused', tone: 'paused' };
    if (member.tone === 'failing') return { label: 'Failing', tone: 'failing' };
    if (member.calls > 0) return { label: 'Working', tone: 'good' };
    return { label: 'On, nothing today', tone: 'idle' };
}

/** The first thing that is not working, or nothing when all is well. */
export function firstFault(items: ReadinessItem[]): ReadinessItem | null {
    return items.find((item) => item.status !== 'ok' && item.status !== 'ready') ?? null;
}

export function AgentStanding({
    workflowId,
    folderId,
}: {
    workflowId: number;
    /** The channel this bot is in. The panel above has already fetched the
     *  workflow, so it hands the id down rather than fetching it twice. */
    folderId?: number | null;
}) {
    const { user, loading: authLoading } = useAuth();
    const fetched = useRef(false);
    const [member, setMember] = useState<TeamMember | null>(null);
    const [fault, setFault] = useState<ReadinessItem | null>(null);
    const [channel, setChannel] = useState<string | null>(null);

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const [team, readiness, folders] = await Promise.all([
                teamStatusApiV1TeamStatusGet({ query: { hours: 24 } }),
                agentReadinessApiV1WorkflowWorkflowIdReadinessGet({ path: { workflow_id: workflowId } }),
                folderId ? listFoldersApiV1FolderGet() : Promise.resolve({ data: [] }),
            ]);
            const mine = team.data?.members?.find((m) => m.workflow_id === workflowId) ?? null;
            setMember(mine);
            if (readiness.data && !readiness.data.ready) setFault(firstFault(readiness.data.items));
            // The channel a bot belongs to is the folder holding it. The id
            // comes down from the panel above; this call is only for its name.
            const held = folders.data?.find((folder) => folder.id === folderId);
            setChannel(held?.name ?? null);
        })();
    }, [authLoading, user, workflowId, folderId]);

    const state = standing(member);

    return (
        <section className="space-y-2" aria-label="How it is doing">
            <div className="flex items-center gap-2 text-sm">
                <span
                    aria-hidden
                    className={cn('h-2 w-2 shrink-0 rounded-full', TONE_DOT[state.tone] ?? TONE_DOT.idle)}
                />
                <span className="font-medium">{state.label}</span>
                {member && member.calls > 0 && (
                    <span className="text-muted-foreground">
                        · {member.answered} of {member.calls} answered today
                    </span>
                )}
            </div>

            {channel && (
                <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
                    <Hash className="h-3.5 w-3.5 shrink-0" aria-hidden />
                    {channel}
                </p>
            )}

            {/* Failures rise. One line, and the whole story is a click away on
                the card that already exists -- not a second copy of it here. */}
            {fault && (
                <p className="flex items-start gap-1.5 text-sm text-[var(--destructive)]">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                    <span>
                        {fault.label} is not working
                        {fault.recent_failures > 0 && ` (${fault.recent_failures} recent)`}.{' '}
                        <Link
                            href={`/workflow/${workflowId}/settings`}
                            className="underline underline-offset-2"
                        >
                            Fix it
                        </Link>
                    </span>
                </p>
            )}

            {!fault && member?.is_live && (
                <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
                    <CircleDot className="h-3.5 w-3.5 shrink-0" aria-hidden />
                    Everything it needs is connected.
                </p>
            )}
        </section>
    );
}

export default AgentStanding;
