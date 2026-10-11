'use client';

/**
 * About this agent: the column beside its chat (the approved design, October
 * 2026). Three things a person changes, and nothing else: its voice, its
 * skills and its memory. Models, quality, triggers and the rest stay on
 * the agent's advanced setup, behind the menu in the header. Owns its data
 * so the chat page does not have to.
 *
 * All three take effect when saved. Skills and memory always did; the voice
 * used to be saved into the agent's draft, which nothing on this page
 * publishes, so it waited silently and went live later with somebody's
 * unrelated edit. The voice row now puts its change live on its own
 * (`PUT /workflow/{id}/voice/live`) and says so.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { getWorkflowApiV1WorkflowFetchWorkflowIdGet } from '@/client/sdk.gen';
import { AgentLearning } from '@/components/agent/AgentLearning';
import { AgentMemory } from '@/components/agent/AgentMemory';
import { AgentSkills } from '@/components/agent/AgentSkills';
import { type Avatar } from '@/components/avatar/avatar';
import { BlobFace } from '@/components/brand/BlobFace';
import { useAuth } from '@/lib/auth';
import type { WorkflowConfigurations } from '@/types/workflow-configurations';

import { ModelRow } from './ModelRow';

export function AboutPanel({
    workflowId,
    name,
}: {
    workflowId: number;
    name: string;
}) {
    const { user, loading: authLoading } = useAuth();
    const fetched = useRef(false);
    const [configurations, setConfigurations] = useState<WorkflowConfigurations | null>(null);
    const [avatar, setAvatar] = useState<Partial<Avatar> | null>(null);

    const fetchAgent = useCallback(async () => {
        const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({ path: { workflow_id: workflowId } });
        if (response.error || !response.data) return;
        setConfigurations((response.data.workflow_configurations ?? null) as WorkflowConfigurations | null);
        setAvatar((response.data as { avatar?: Partial<Avatar> | null }).avatar ?? null);
    }, [workflowId]);

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void fetchAgent();
    }, [authLoading, user, fetchAgent]);

    return (
        <div className="flex h-full flex-col gap-6 overflow-y-auto px-1 pb-6" data-testid="about-agent">
            <div className="flex items-center gap-3 pt-1">
                <BlobFace seed={workflowId} avatar={avatar} size={48} />
                <span className="min-w-0 truncate text-base font-semibold">{name}</span>
            </div>
            <section aria-labelledby="agent-voice" className="space-y-2">
                <h3 id="agent-voice" className="text-[13px] font-normal text-muted-foreground">
                    Voice
                </h3>
                <ModelRow
                    workflowId={workflowId}
                    editable
                    voiceOnly
                    configurations={configurations}
                    // The voice's own settings (pronunciations, background
                    // sound) went live with it; re-read so the panel opens on
                    // them next time.
                    onVoiceApplied={() => void fetchAgent()}
                />
            </section>
            <AgentSkills workflowId={workflowId} agentName={name} />
            <AgentMemory workflowId={workflowId} agentName={name} />
            <AgentLearning workflowId={workflowId} />
        </div>
    );
}

export default AboutPanel;
