'use client';

/**
 * About this bot: the profile column the Chat tab opens at the side, the
 * way Slack opens a teammate's profile beside the conversation. Owns its
 * data so the chat page does not have to: the bot's definition for the
 * skills and documents, its configuration for the brains and voice, and a
 * save path for the pencils on those tiles.
 */

import { X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
    getWorkflowApiV1WorkflowFetchWorkflowIdGet,
    updateWorkflowApiV1WorkflowWorkflowIdPut,
} from '@/client/sdk.gen';
import type { FlowNode } from '@/components/flow/types';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/lib/auth';
import type { WorkflowConfigurations } from '@/types/workflow-configurations';

import { AgentProfilePanel } from './AgentProfilePanel';
import { ModelRow } from './ModelRow';

export function AboutPanel({
    workflowId,
    name,
    onClose,
}: {
    workflowId: number;
    name: string;
    onClose: () => void;
}) {
    const { user, loading: authLoading } = useAuth();
    const fetched = useRef(false);
    const [nodes, setNodes] = useState<FlowNode[]>([]);
    const [configurations, setConfigurations] = useState<WorkflowConfigurations | null>(null);

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({ path: { workflow_id: workflowId } });
            if (response.error || !response.data) return;
            const definition = response.data.workflow_definition as { nodes?: FlowNode[] } | null;
            setNodes(definition?.nodes ?? []);
            setConfigurations((response.data.workflow_configurations ?? null) as WorkflowConfigurations | null);
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
        <div className="flex h-full flex-col">
            <div className="flex items-center justify-between border-b border-border px-5 py-3">
                <p className="text-sm font-semibold">About</p>
                <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}>
                    <X className="h-4 w-4" />
                </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
                <AgentProfilePanel workflowId={workflowId} name={name} nodes={nodes}>
                    <ModelRow
                        workflowId={workflowId}
                        editable
                        configurations={configurations}
                        onSaveConfigurations={save}
                        stacked
                    />
                </AgentProfilePanel>
            </div>
        </div>
    );
}

export default AboutPanel;
