'use client';

/**
 * Every channel in the account, with the door to make another.
 *
 * Only `/channels/[folderId]` existed, so `/channels` -- the "Channels"
 * heading in the rail, the "N more" row under it, or the address bar with the
 * id taken off -- was Next's "page not found". The rail holds eight; this is
 * where the rest are.
 *
 * Same source as the rail (`GET /folder`) so the two can never disagree about
 * which channels exist. The agent count comes from the agents list; if that
 * one fails the channels still show, just without counts.
 */

import { Hash, Plus } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

import { getWorkflowsApiV1WorkflowFetchGet, listFoldersApiV1FolderGet } from '@/client/sdk.gen';
import type { FolderResponse } from '@/client/types.gen';
import { NewChatDialog } from '@/components/layout/NewChatDialog';
import { PageBody, PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/button';
import { detailFromResult } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

/** "1 agent", "3 agents", nothing when the count is unknown. */
function agentCountLabel(count: number | undefined): string | null {
    if (count === undefined) return null;
    return count === 1 ? '1 agent' : `${count} agents`;
}

export default function ChannelsPage() {
    const { user, loading: authLoading } = useAuth();
    const [channels, setChannels] = useState<FolderResponse[]>([]);
    const [counts, setCounts] = useState<Record<number, number> | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [newChatOpen, setNewChatOpen] = useState(false);
    const started = useRef(false);

    useEffect(() => {
        // Wait for auth: the interceptor that attaches the token is registered
        // only once auth has loaded -- see ui/AGENTS.md.
        if (authLoading || !user || started.current) return;
        started.current = true;
        let cancelled = false;
        void (async () => {
            try {
                const [folders, workflows] = await Promise.all([
                    listFoldersApiV1FolderGet(),
                    getWorkflowsApiV1WorkflowFetchGet(),
                ]);
                if (cancelled) return;
                if (folders.error) {
                    setError(detailFromResult(folders, 'Could not load your channels.'));
                    return;
                }
                setChannels(folders.data ?? []);
                if (!workflows.error) {
                    const tally: Record<number, number> = {};
                    for (const workflow of workflows.data ?? []) {
                        if (workflow.folder_id == null) continue;
                        tally[workflow.folder_id] = (tally[workflow.folder_id] ?? 0) + 1;
                    }
                    setCounts(tally);
                }
            } catch {
                if (!cancelled) setError('Could not load your channels.');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user]);

    const newChannel = (
        <Button size="sm" onClick={() => setNewChatOpen(true)}>
            <Plus aria-hidden="true" className="h-4 w-4" />
            New channel
        </Button>
    );

    return (
        <>
            <PageHeader
                title="Channels"
                description="Places where you and several agents work together."
                actions={newChannel}
            />
            <PageBody>
                {loading ? (
                    <p className="text-sm text-muted-foreground">Loading channels…</p>
                ) : error ? (
                    <p className="text-sm text-destructive" role="alert">
                        {error}
                    </p>
                ) : channels.length === 0 ? (
                    <div className="mx-auto max-w-md rounded-xl border border-dashed border-border px-6 py-10 text-center">
                        <Hash aria-hidden="true" className="mx-auto h-6 w-6 text-muted-foreground" />
                        <h2 className="mt-3 text-base font-medium text-foreground">No channels yet</h2>
                        <p className="mt-1 text-sm text-muted-foreground">
                            Pick two or more agents and they share a channel: you ask, they answer in one place.
                        </p>
                        <div className="mt-5 flex justify-center">{newChannel}</div>
                    </div>
                ) : (
                    <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
                        {channels.map((channel) => {
                            const countLabel = counts ? agentCountLabel(counts[channel.id] ?? 0) : null;
                            return (
                                <li key={channel.id}>
                                    <Link
                                        href={`/channels/${channel.id}`}
                                        className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-muted/60"
                                    >
                                        <span className="min-w-0 truncate text-sm text-foreground">
                                            <span aria-hidden="true" className="text-muted-foreground">
                                                #{' '}
                                            </span>
                                            {channel.name}
                                        </span>
                                        {countLabel && (
                                            <span className="shrink-0 text-xs text-muted-foreground">{countLabel}</span>
                                        )}
                                    </Link>
                                </li>
                            );
                        })}
                    </ul>
                )}
            </PageBody>
            {/* Mounted on demand, as in the rail: the dialog brings its own
                router and fetch, which a closed door does not need. */}
            {newChatOpen && <NewChatDialog open onOpenChange={setNewChatOpen} />}
        </>
    );
}
