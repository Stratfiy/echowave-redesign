"use client";

/** A bot's own numbers: how much it ran, and what it cost. */
import { useParams } from "next/navigation";

import { AgentHeader } from "../components/AgentHeader";
import { AgentTabs } from "../components/AgentTabs";
import { BotAnalytics } from "./BotAnalytics";

export default function WorkflowAnalyticsPage() {
    const { workflowId } = useParams();
    const id = Number(workflowId);

    return (
        <>
            <AgentHeader workflowId={id} name="" />
            <AgentTabs workflowId={id} />
            <BotAnalytics workflowId={id} />
        </>
    );
}
