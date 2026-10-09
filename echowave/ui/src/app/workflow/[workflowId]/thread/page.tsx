'use client';

import { Info, MoreHorizontal, Phone } from 'lucide-react';
import Link from 'next/link';
import { use, useCallback, useEffect, useRef, useState } from 'react';

import { AboutPanel } from '@/app/workflow/[workflowId]/components/AboutPanel';
import { AgentHeader } from '@/app/workflow/[workflowId]/components/AgentHeader';
import { getWorkflowApiV1WorkflowFetchWorkflowIdGet } from '@/client/sdk.gen';
import type { Avatar } from '@/components/avatar/avatar';
import { BlobFace } from '@/components/brand/BlobFace';
import { ChannelComposer } from '@/components/channel/ChannelComposer';
import { ChannelStream } from '@/components/channel/ChannelStream';
import { HuddlePanel, HuddleStrip } from '@/components/huddle/HuddlePanel';
import { useHuddle } from '@/components/huddle/useHuddle';
import { AuxiliaryPanel } from '@/components/layout/AuxiliaryPanel';
import { colleagueState, RAIL_COPY } from '@/components/layout/v2/homes';
import { Button } from '@/components/ui/button';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useTeamStatus } from '@/components/workflow/useTeamStatus';
import { useAuth } from '@/lib/auth';
import { useFeature } from '@/lib/features';

/**
 * The bot's chat -- where it opens.
 *
 * A chat: the bot's thread oldest-first with a composer under it, the same
 * two pieces a channel is made of, pointed at one bot instead of a channel.
 * What you type here goes to this bot and nowhere else -- it is not filed in
 * the channel the bot sits in -- and the bot answers with this thread as its
 * context. Calls, outcomes and decisions appear in the same thread.
 *
 * The agent's page (the approved design, October 2026): its chat, and
 * beside it the three things a person changes -- voice, skills, memory
 * (AboutPanel), open from the start on a wide screen. No tab strip: **Test**
 * rings it, and the menu holds sharing, its activity and the advanced setup
 * (instructions, models, triggers, quality) for whoever needs them.
 *
 * With `huddle` on, the call button opens a huddle instead (components/
 * huddle): a voice conversation with the agent as a teammate, docked beside
 * the thread -- the whole screen on a phone -- with its customer test one
 * switch away. Its proposed changes arrive as cards in this thread. It works
 * for chat and scheduled agents too: the agent talks about its work,
 * whatever channel the work is on.
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
    // A chat agent answers only in writing: no phone test to offer.
    const [chatOnly, setChatOnly] = useState(false);
    // The face its owner picked, if any: the blob keeps its colour.
    const [avatar, setAvatar] = useState<Partial<Avatar> | null>(null);
    const member = useTeamStatus()[id];
    const started = useRef(false);
    const refreshStream = useRef<() => void>(() => {});
    // Open beside the chat on a wide screen; a button away on a narrow one.
    const [aboutOpen, setAboutOpen] = useState(false);
    useEffect(() => {
        if (typeof window !== 'undefined' && window.matchMedia?.('(min-width: 1024px)').matches) setAboutOpen(true);
    }, []);
    const [waitingFor, setWaitingFor] = useState<{ since: string; bots: number[] } | null>(null);
    const huddleOn = useFeature('huddle');
    const [huddleOpen, setHuddleOpen] = useState(false);
    const [notesVersion, setNotesVersion] = useState(0);
    const huddle = useHuddle({
        workflowId: id,
        // The card is on the thread now: show it without waiting for the poll.
        onCard: () => refreshStream.current(),
        onNote: () => setNotesVersion((v) => v + 1),
    });
    const huddlePhase = huddle.state.phase;
    useEffect(() => {
        // The huddle's transcript row is final once it ends.
        if (huddlePhase === 'ended') refreshStream.current();
    }, [huddlePhase]);

    useEffect(() => {
        if (authLoading || !user || started.current) return;
        started.current = true;
        void (async () => {
            const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                path: { workflow_id: id },
            });
            if (!response.error && response.data?.name) setName(response.data.name);
            if (!response.error) setAvatar((response.data as { avatar?: Partial<Avatar> | null } | undefined)?.avatar ?? null);
            const configurations = response.data?.workflow_configurations as { channel?: string } | null | undefined;
            if (!response.error && configurations?.channel === 'chat') setChatOnly(true);
        })();
    }, [authLoading, user, id]);

    const registerRefresh = useCallback((refresh: () => void) => {
        refreshStream.current = refresh;
    }, []);

    const botName = name || 'this agent';

    return (
        <div className="flex h-full flex-col">
            <AgentHeader
                workflowId={id}
                name={name || 'Agent'}
                backHref="/workflow"
                face={<BlobFace seed={id} avatar={avatar} size={26} mood={member?.tone === 'paused' ? 'resting' : 'awake'} />}
                status={member ? RAIL_COPY.state[colleagueState(member)] : null}
                actions={
                    <>
                        <Button
                            size="sm"
                            variant={aboutOpen ? 'secondary' : 'outline'}
                            aria-pressed={aboutOpen}
                            onClick={() => {
                                setAboutOpen((open) => !open);
                                setHuddleOpen(false);
                            }}
                            className="rounded-full"
                        >
                            <Info className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                            About
                        </Button>
                        {huddleOn ? (
                            <Button
                                size="sm"
                                variant={huddleOpen ? 'secondary' : 'outline'}
                                aria-pressed={huddleOpen}
                                aria-label={`Huddle with ${botName}`}
                                data-testid="huddle-button"
                                onClick={() => {
                                    setHuddleOpen(true);
                                    setAboutOpen(false);
                                }}
                                className="rounded-full"
                            >
                                <Phone className="h-3.5 w-3.5 sm:mr-1.5" aria-hidden />
                                <span className="hidden sm:inline">Huddle</span>
                            </Button>
                        ) : !chatOnly && (
                            <Button asChild size="sm" variant="outline" className="rounded-full">
                                <Link href={`/workflow/${id}?onboarding=web_call`}>
                                    <Phone className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                                    Call me to test
                                </Link>
                            </Button>
                        )}
                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <Button size="icon" variant="ghost" aria-label={`More for ${botName}`} className="rounded-full">
                                    <MoreHorizontal className="h-4 w-4" />
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" className="w-52">
                                <DropdownMenuItem asChild>
                                    <Link href={`/workflow/${id}/settings?tab=share`}>Share</Link>
                                </DropdownMenuItem>
                                <DropdownMenuItem asChild>
                                    <Link href={`/workflow/${id}/runs`}>Activity</Link>
                                </DropdownMenuItem>
                                <DropdownMenuItem asChild>
                                    <Link href={`/workflow/${id}`}>Advanced setup</Link>
                                </DropdownMenuItem>
                            </DropdownMenuContent>
                        </DropdownMenu>
                    </>
                }
            />
            <div className="flex min-h-0 flex-1">
                <div className="flex min-w-0 flex-1 flex-col">
                    <ChannelStream
                        workflowId={id}
                        botNames={{ [id]: botName }}
                        onRegisterRefresh={registerRefresh}
                        waitingFor={waitingFor}
                    />
                    {huddleOn && !huddleOpen && (
                        <HuddleStrip agentName={botName} huddle={huddle} onOpen={() => setHuddleOpen(true)} />
                    )}
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
                {huddleOn && huddleOpen && (
                    <AuxiliaryPanel label={`Huddle with ${botName}`} onClose={() => setHuddleOpen(false)}>
                        <HuddlePanel
                            workflowId={id}
                            agentName={botName}
                            chatOnly={chatOnly}
                            huddle={huddle}
                            notesVersion={notesVersion}
                        />
                    </AuxiliaryPanel>
                )}
                {aboutOpen && !huddleOpen && (
                    <AuxiliaryPanel
                        label="About this agent"
                        onClose={() => setAboutOpen(false)}
                    >
                        <AboutPanel workflowId={id} name={botName} />
                    </AuxiliaryPanel>
                )}
            </div>
        </div>
    );
}
