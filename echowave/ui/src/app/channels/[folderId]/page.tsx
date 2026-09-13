'use client';

/**
 * A channel: who is in it, what has happened, and a box to type in.
 *
 * The backend for this has been complete and deployed for a while —
 * `GET /timeline?folder_id=`, `POST /timeline/message`, handle resolution and
 * the worker that answers. Until this page existed, @mentions were a thing the
 * API could do and the product could not, which is the same write-only shape
 * the timeline itself was in before `BotThread`: the capability shipped, and
 * nothing let a customer reach it.
 *
 * The roster is the bots filed in this channel, and it is load-bearing rather
 * than informational: the server will only resolve a mention against bots in
 * this channel, so a page that listed every bot in the account would offer
 * people handles that answer to nothing.
 */

import { ArrowLeft, Bot } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
    getWorkflowsApiV1WorkflowFetchGet,
    listFoldersApiV1FolderGet,
} from '@/client/sdk.gen';
import type { ChannelBot } from '@/components/channel/ChannelComposer';
import { ChannelComposer, handleOf } from '@/components/channel/ChannelComposer';
import { ChannelStream } from '@/components/channel/ChannelStream';
import { detailFromResult } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

export default function ChannelPage() {
    const params = useParams<{ folderId: string }>();
    const folderId = Number(params?.folderId);
    const { user, loading: authLoading } = useAuth();

    const [name, setName] = useState<string | null>(null);
    const [bots, setBots] = useState<ChannelBot[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const fetched = useRef(false);
    const refreshStream = useRef<() => void>(() => {});

    useEffect(() => {
        // Wait for auth: the interceptor that attaches the token is registered
        // only once auth has loaded — see ui/AGENTS.md.
        if (authLoading || !user || fetched.current || !Number.isFinite(folderId)) return;
        fetched.current = true;
        void (async () => {
            const [folders, workflows] = await Promise.all([
                listFoldersApiV1FolderGet(),
                getWorkflowsApiV1WorkflowFetchGet(),
            ]);
            if (folders.error) {
                setError(detailFromResult(folders, 'Could not load this channel'));
            } else {
                const here = (folders.data ?? []).find((f) => f.id === folderId);
                // Named rather than left blank when it is missing: "Channel"
                // over an empty heading at least tells the reader the page
                // loaded and the name did not.
                setName(here?.name ?? 'Channel');
            }
            if (workflows.error) {
                setError(detailFromResult(workflows, 'Could not load the bots here'));
            } else {
                setBots(
                    (workflows.data ?? [])
                        .filter((workflow) => workflow.folder_id === folderId)
                        .map((workflow) => ({
                            id: workflow.id,
                            name: workflow.name,
                            handle: workflow.handle,
                        })),
                );
            }
            setLoading(false);
        })();
    }, [authLoading, user, folderId]);

    const registerRefresh = useCallback((refresh: () => void) => {
        refreshStream.current = refresh;
    }, []);

    if (!Number.isFinite(folderId)) {
        return <p className="px-6 py-8 text-sm text-destructive">No such channel.</p>;
    }

    const botNames = Object.fromEntries(bots.map((bot) => [bot.id, bot.name]));

    return (
        // h-full and a column, because the stream scrolls and the composer
        // stays put. A page that scrolls as a whole puts the box you type in
        // below the fold of a busy channel.
        <div className="flex h-full flex-col">
            <header className="border-b border-border px-6 pb-3 pt-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                        <h1 className="truncate text-[26px] leading-tight text-foreground">
                            <span className="text-muted-foreground">#</span>{' '}
                            {name ?? '…'}
                        </h1>
                        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
                            {loading ? (
                                'Loading…'
                            ) : bots.length === 0 ? (
                                'No bots in this channel yet — move one here from Bots to ask it for things.'
                            ) : (
                                <>
                                    <Bot aria-hidden className="h-3.5 w-3.5" />
                                    {bots.map((bot) => (
                                        <Link
                                            key={bot.id}
                                            href={`/workflow/${bot.id}`}
                                            className="underline-offset-2 hover:underline"
                                            title={bot.name}
                                        >
                                            @{handleOf(bot)}
                                        </Link>
                                    ))}
                                </>
                            )}
                        </p>
                    </div>
                    <Link
                        href="/workflow"
                        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
                    >
                        <ArrowLeft className="h-4 w-4" />
                        All bots
                    </Link>
                </div>
            </header>

            {error && (
                <p className="px-6 pt-3 text-sm text-destructive" role="alert">
                    {error}
                </p>
            )}

            <ChannelStream
                folderId={folderId}
                botNames={botNames}
                onRegisterRefresh={registerRefresh}
            />

            <ChannelComposer
                folderId={folderId}
                bots={bots}
                channelName={name ?? 'channel'}
                onSent={() => refreshStream.current()}
            />
        </div>
    );
}
