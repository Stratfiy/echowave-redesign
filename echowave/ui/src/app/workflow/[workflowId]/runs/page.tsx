"use client";

import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

import { BotResults } from "@/components/workflow/BotResults";

import { AgentHeader } from "../components/AgentHeader";
import { AgentTabs } from "../components/AgentTabs";
import { WorkflowExecutions } from "../components/WorkflowExecutions";

export default function WorkflowRunsPage() {
    const { workflowId } = useParams();
    const searchParams = useSearchParams();
    const [name, setName] = useState("");
    // The list below already fetches the agent, for its disposition filter.
    // Taking the name from that beats a second request for a title.
    const handleName = useCallback((value: string) => setName(value), []);

    return (
        <>
            {/* The same bar and the same strip as every other tab of this
                agent. This screen used to have neither, so Logs was the one
                place an agent stopped looking like one thing. */}
            <AgentHeader workflowId={Number(workflowId)} name={name} />
            <AgentTabs workflowId={Number(workflowId)} />
            {/* Above the run list, not beside it: the reading order is the
                order the questions come. What did this bot achieve, then
                which calls were those. */}
            <div className="px-6 pt-6">
                <BotResults workflowId={Number(workflowId)} />
            </div>
            <WorkflowExecutions
                workflowId={Number(workflowId)}
                searchParams={searchParams}
                onWorkflowName={handleName}
            />
        </>
    );
}
