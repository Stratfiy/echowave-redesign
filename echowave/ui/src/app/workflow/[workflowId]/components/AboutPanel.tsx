'use client';

/**
 * About this agent: the column beside its chat (the approved design, October
 * 2026). Three things a person changes, and nothing else: its voice, its
 * skills and its memory. Models, quality, triggers and the rest stay on
 * the agent's advanced setup, behind the menu in the header. Owns its data
 * so the chat page does not have to.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
    getWorkflowApiV1WorkflowFetchWorkflowIdGet,
    updateWorkflowApiV1WorkflowWorkflowIdPut,
} from '@/client/sdk.gen';
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

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({ path: { workflow_id: workflowId } });
            if (response.error || !response.data) return;
            setConfigurations((response.data.workflow_configurations ?? null) as WorkflowConfigurations | null);
            setAvatar((response.data as { avatar?: Partial<Avatar> | null }).avatar ?? null);
        })();
    }, [authLoading, user, workflowId]);

    const save = useCallback(
        async (patch: Partial<WorkflowConfigurations>) => {
            const merged = { ...(configurations ?? {}), ...patch } as WorkflowConfigurations;
            const response = await updateWorkflowApiV1WorkflowWorkflowIdPut({
                path: { workflow_id: workflowId },
                body: {
                    name,
                    workflow_definition: null,
                    workflow_configurations: merged as Record<string, unknown>,
                },
            });
            if (response.error) {
                const detail = (response.error as { detail?: unknown }).detail;
                throw new Error(typeof detail === 'string' ? detail : 'Could not save these settings');
            }
            setConfigurations(merged);
        },
        [configurations, name, workflowId],
    );

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
                    onSaveConfigurations={save}
                />
            </section>
            <AgentSkills workflowId={workflowId} agentName={name} />
            <AgentMemory workflowId={workflowId} agentName={name} />
        </div>
    );
}

export default AboutPanel;
