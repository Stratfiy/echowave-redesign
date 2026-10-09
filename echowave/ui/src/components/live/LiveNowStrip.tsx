'use client';

/**
 * "Live now": the calls in progress, where calls already show -- an agent's
 * thread (its own calls) and Today (the workspace's). Tap one and the listen
 * panel opens right here, under the strip; nothing navigates away.
 *
 * Draws nothing while the feature is off or no call is in progress. A list
 * that failed to load says so rather than looking empty.
 */

import { Globe,Headphones, PhoneIncoming, PhoneOutgoing } from 'lucide-react';
import { useEffect, useState } from 'react';

import type { LiveCallItem } from '@/client/types.gen';
import { cn } from '@/lib/utils';

import { BLOCKED_COPY, callerLabel, ListenPanel } from './ListenPanel';
import { duration } from './transcript';
import { useLiveCalls } from './useLiveCalls';

function useNow(on: boolean): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!on) return;
        const timer = window.setInterval(() => setNow(Date.now()), 1000);
        return () => window.clearInterval(timer);
    }, [on]);
    return now;
}

export function elapsed(call: Pick<LiveCallItem, 'started_at' | 'duration_seconds'>, now: number): number {
    const started = Date.parse(call.started_at);
    return Number.isFinite(started) ? (now - started) / 1000 : call.duration_seconds;
}

const ICON = { inbound: PhoneIncoming, outbound: PhoneOutgoing, web: Globe } as const;

export function LiveNowStrip({ workflowId, className }: { workflowId?: number; className?: string }) {
    const { on, data, error, reload } = useLiveCalls(workflowId);
    const [open, setOpen] = useState<LiveCallItem | null>(null);
    const calls = data?.calls ?? [];
    const now = useNow(on && calls.length > 0);

    if (!on) return null;
    if (error && !data) {
        return (
            <p role="alert" className={cn('text-xs text-destructive', className)}>
                {error}
            </p>
        );
    }
    if (!calls.length && !open) return null;

    return (
        <section aria-label="Live now" className={cn('flex flex-col gap-2', className)}>
            <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <span className="relative flex h-2 w-2" aria-hidden>
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-60 motion-reduce:animate-none" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-red-500" />
                </span>
                Live now{calls.length ? ` · ${calls.length}` : ''}
            </h2>
            {calls.length > 0 && (
                <ul className="flex flex-col gap-1">
                    {calls.map((call) => {
                        const Icon = ICON[call.direction as keyof typeof ICON] ?? PhoneIncoming;
                        const selected = open?.run_id === call.run_id;
                        return (
                            <li key={call.run_id}>
                                <button
                                    type="button"
                                    onClick={() => setOpen(selected ? null : call)}
                                    aria-expanded={selected}
                                    className={cn(
                                        'flex min-h-11 w-full items-center gap-3 rounded-lg border border-border px-3 py-2 text-left hover:bg-muted/60',
                                        selected && 'bg-muted/60',
                                    )}
                                >
                                    <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-sm font-medium">
                                            {workflowId === undefined ? `${call.agent_name} · ` : ''}
                                            {callerLabel(call)}
                                        </span>
                                        <span className="block truncate text-xs text-muted-foreground">
                                            {call.step ?? 'On the call'}
                                            {call.blocked ? ` · ${BLOCKED_COPY[call.blocked] ?? ''}` : ''}
                                        </span>
                                    </span>
                                    <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
                                        {duration(elapsed(call, now))}
                                    </span>
                                    {call.can_listen && <Headphones className="h-4 w-4 shrink-0" aria-label="Listen" />}
                                </button>
                            </li>
                        );
                    })}
                </ul>
            )}
            {data?.more && (
                <p className="text-xs text-muted-foreground">More calls are live than are shown here.</p>
            )}
            {open && (
                <ListenPanel
                    key={open.run_id}
                    call={calls.find((c) => c.run_id === open.run_id) ?? open}
                    canChangeSetting={Boolean(data?.can_change_setting)}
                    onClose={() => setOpen(null)}
                    onSettingChanged={() => {
                        void (async () => {
                            await reload();
                        })();
                    }}
                />
            )}
        </section>
    );
}
