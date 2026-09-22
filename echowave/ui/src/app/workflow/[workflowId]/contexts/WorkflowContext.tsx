import { createContext, useContext } from 'react';

import type { DocumentResponseSchema, ToolResponse } from '@/client/types.gen';
import type { RecordingResponseSchema } from '@/client/types.gen';
import type { BotChannel } from '@/components/flow/channelWords';

interface WorkflowContextType {
    saveWorkflow: (updateWorkflowDefinition?: boolean) => Promise<void>;
    documents?: DocumentResponseSchema[];
    tools?: ToolResponse[];
    updateTool?: (
        toolUuid: string,
        updater: (tool: ToolResponse) => ToolResponse,
    ) => void;
    recordings?: RecordingResponseSchema[];
    /** This agent's stable reference, so a hand-over cannot offer itself. */
    workflowUuid?: string;
    readOnly?: boolean;
    /** Phone or writing. Decides the words the canvas uses for its node
     * kinds; absent reads as voice, as the API reads it. */
    channel?: BotChannel;
}

const WorkflowContext = createContext<WorkflowContextType | undefined>(undefined);

export const WorkflowProvider = WorkflowContext.Provider;

export const useWorkflow = () => {
    const context = useContext(WorkflowContext);
    if (!context) {
        throw new Error('useWorkflow must be used within a WorkflowProvider');
    }
    return context;
};

// Optional hook that doesn't throw if context is not available
export const useWorkflowOptional = () => {
    return useContext(WorkflowContext);
};
