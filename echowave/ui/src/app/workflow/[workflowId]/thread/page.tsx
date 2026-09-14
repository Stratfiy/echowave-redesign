'use client';

import { Phone, Share2 } from 'lucide-react';
import Link from 'next/link';
import { use, useEffect, useRef, useState } from 'react';

import { AgentHeader } from '@/app/workflow/[workflowId]/components/AgentHeader';
import { AgentTabs } from '@/app/workflow/[workflowId]/components/AgentTabs';
import { getWorkflowApiV1WorkflowFetchWorkflowIdGet } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import { BotThread } from '@/components/workflow/BotThread';
import { useAuth } from '@/lib/auth';

/**
 * The bot's chat -- where it opens.
 *
 * Two doors at the top, because they are the two things somebody does with a
 * bot before trusting it with a real caller: **Test** rings it (the tester
 * on the editor, opened on arrival) and **Share** hands out the link. They
 * lived on the editor and the settings page; a bot that opens on its chat
 * needs them where it opens.
 *
 * The strip of tabs is here too. It was not, which meant a bot opened from
 * the rail had no way to its own instructions.
 */
export default function BotThreadPage({
    params,
}: {
    params: Promise<{ workflowId: string }>;
}) {
    const { workflowId } = use(params);
    const id = Number(workflowId);
    const { user, loading: authLoading } = useAuth();
    const [name, setName] = useState<string>('');
    const started = useRef(false);

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

    return (
        <div className="flex h-full flex-col">
            <AgentHeader workflowId={id} name={name || 'Bot'} />
            <AgentTabs workflowId={id} />
            <div className="mx-auto w-full max-w-3xl px-6 py-6">
                <div className="flex items-center justify-between gap-3">
                    <h1 className="text-lg font-semibold">What this bot has been doing</h1>
                    <div className="flex shrink-0 gap-2">
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
                <BotThread workflowId={id} />
            </div>
        </div>
    );
}
