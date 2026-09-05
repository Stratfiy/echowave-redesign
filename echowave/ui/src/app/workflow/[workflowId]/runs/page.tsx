"use client";

import { useParams, useSearchParams } from "next/navigation";

import WorkflowLayout from "../../WorkflowLayout";
import { AgentTabs } from "../components/AgentTabs";
import { WorkflowExecutions } from "../components/WorkflowExecutions";

export default function WorkflowRunsPage() {
    const { workflowId } = useParams();
    const searchParams = useSearchParams();

    return (
        <WorkflowLayout showFeaturesNav={false}>
            {/* Same strip as the editor: this is the agent's Logs tab, not a
                separate screen that happens to be about an agent. */}
            <AgentTabs workflowId={Number(workflowId)} />
            <WorkflowExecutions
                workflowId={Number(workflowId)}
                searchParams={searchParams}
            />
        </WorkflowLayout>
    );
}
