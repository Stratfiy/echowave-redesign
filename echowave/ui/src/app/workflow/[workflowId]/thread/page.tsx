'use client';

import { use } from 'react';

import { BotThread } from '@/components/workflow/BotThread';

export default function BotThreadPage({
    params,
}: {
    params: Promise<{ workflowId: string }>;
}) {
    const { workflowId } = use(params);
    return (
        <div className="mx-auto w-full max-w-3xl px-6 py-6">
            <h1 className="text-lg font-semibold">What this bot has been doing</h1>
            <BotThread workflowId={Number(workflowId)} />
        </div>
    );
}
