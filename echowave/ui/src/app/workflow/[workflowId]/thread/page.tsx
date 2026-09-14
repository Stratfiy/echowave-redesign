'use client';

import { Info, Phone, Share2 } from 'lucide-react';
import Link from 'next/link';
import { use, useCallback, useEffect, useRef, useState } from 'react';

import { AboutPanel } from '@/app/workflow/[workflowId]/components/AboutPanel';
import { AgentHeader } from '@/app/workflow/[workflowId]/components/AgentHeader';
import { AgentTabs } from '@/app/workflow/[workflowId]/components/AgentTabs';
import { getWorkflowApiV1WorkflowFetchWorkflowIdGet } from '@/client/sdk.gen';
import { ChannelComposer } from '@/components/channel/ChannelComposer';
import { ChannelStream } from '@/components/channel/ChannelStream';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/lib/auth';

/**
 * The bot's chat -- where it opens.
 *
 * A chat: the bot's thread oldest-first with a composer under it, the same
 * two pieces a channel is made of, pointed at one bot instead of a channel.
 * What you type here goes to this bot and nowhere else -- it is not filed in
 * the channel the bot sits in -- and the bot answers with this thread as its
 * context. Calls, outcomes and decisions appear in the same thread.
 *
 * Two doors at the top, because they are the two things somebody does with a
 * bot before trusting it with a real caller: **Test** rings it (the tester
 * on the editor, opened on arrival) and **Share** hands out the link.
 */
export default function BotChatPage({
    params,
}: {
    params: Promise<{ workflowId: string }>;
}) {
    const { workflowId } = use(params);
    const id = Number(workflowId);
    const { user, loading: authLoading } = useAuth();
    const [name, setName] = useState<string>('');
    const started = useRef(false);
    const refreshStream = useRef<() => void>(() => {});
    const [aboutOpen, setAboutOpen] = useState(false);
    const [waitingFor, setWaitingFor] = useState<{ since: string; bots: number[] } | null>(null);

    useEffect(() => {
        if (authLoading || !user || started.current) return;
        started.current = true;
        void (async () => {
            const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                path: { workflow_id: id },
            });
            if (!response.error && response.data?.name) setName(response.data.name);
        })();
    }, [authLoading, user, id]);

    const registerRefresh = useCallback((refresh: () => void) => {
        refreshStream.current = refresh;
    }, []);

    const botName = name || 'this bot';

    return (
        <div className="flex h-full flex-col">
            <AgentHeader workflowId={id} name={name || 'Bot'} />
            <div className="flex items-center justify-between gap-3 border-b border-border pr-6">
                <AgentTabs workflowId={id} />
                <div className="flex shrink-0 gap-2 py-1.5">
                    {/* Who this bot is -- skills, knowledge, memory, brains
                        and voice -- opens beside the chat, like a teammate's
                        profile in Slack. */}
                    <Button
                        size="sm"
                        variant={aboutOpen ? 'secondary' : 'outline'}
                        aria-pressed={aboutOpen}
                        onClick={() => setAboutOpen((open) => !open)}
                    >
                        <Info className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                        About
                    </Button>
                    <Button asChild size="sm" variant="outline">
                        <Link href={`/workflow/${id}?onboarding=web_call`}>
                            <Phone className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                            Test
                        </Link>
                    </Button>
                    <Button asChild size="sm" variant="outline">
                        <Link href={`/workflow/${id}/settings?tab=share`}>
                            <Share2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                            Share
                        </Link>
                    </Button>
                </div>
            </div>
            <div className="flex min-h-0 flex-1">
                <div className="flex min-w-0 flex-1 flex-col">
                    <ChannelStream
                        workflowId={id}
                        botNames={{ [id]: botName }}
                        onRegisterRefresh={registerRefresh}
                        waitingFor={waitingFor}
                    />
                    <ChannelComposer
                        workflowId={id}
                        bots={[]}
                        channelName={botName}
                        onSent={(asked) => {
                            setWaitingFor(asked.length ? { since: new Date().toISOString(), bots: asked } : null);
                            refreshStream.current();
                        }}
                    />
                </div>
                {aboutOpen && (
                    <aside className="w-full max-w-[400px] shrink-0 border-l border-border bg-muted/20" aria-label="About this bot">
                        <AboutPanel workflowId={id} name={botName} onClose={() => setAboutOpen(false)} />
                    </aside>
                )}
            </div>
        </div>
    );
}
