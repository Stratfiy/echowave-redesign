"use client";

/**
 * Which of your agents takes the call from here.
 *
 * Fetches its own list rather than taking one through the renderer context.
 * Every other reference type in that context — tools, documents, recordings —
 * is loaded once for the whole canvas because most nodes need them; a handoff
 * is rare, and putting agents in there would mean fetching them on every
 * canvas open for the graphs that have no handoff at all, which is nearly all
 * of them.
 *
 * The list is `/workflow/summary`, which is already the org-scoped "your
 * agents". That scoping is the same reason the backend refuses a handoff
 * naming an agent from another account — this just means the picker cannot
 * offer one in the first place.
 */

import { useEffect, useRef, useState } from "react";

import { getWorkflowsSummaryApiV1WorkflowSummaryGet } from "@/client/sdk.gen";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useAuth } from "@/lib/auth";
import logger from "@/lib/logger";

type Agent = { uuid: string; name: string };

/** `workflow_uuid` until the generated client is next rebuilt.
 *
 * Regeneration needs a running backend, so the field is declared here rather
 * than hand-edited into `src/client/types.gen.ts`, which a regeneration would
 * silently revert. Delete this once the generated type carries it. */
type SummaryWithUuid = { workflow_uuid?: string | null; name: string };

export function AgentRefSelect({
    value,
    onChange,
    excludeUuid,
}: {
    value: string;
    onChange: (value: string) => void;
    /** This agent, which cannot hand off to itself. */
    excludeUuid?: string;
}) {
    const { user, loading: authLoading } = useAuth();
    const [agents, setAgents] = useState<Agent[] | null>(null);
    const hasFetched = useRef(false);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;

        const load = async () => {
            const response = await getWorkflowsSummaryApiV1WorkflowSummaryGet({
                query: { status: "active" },
            });
            if (response.error) {
                logger.error(`Error loading agents: ${JSON.stringify(response.error)}`);
                setAgents([]);
                return;
            }
            setAgents(
                ((response.data ?? []) as unknown as SummaryWithUuid[])
                    .filter((row) => Boolean(row.workflow_uuid))
                    .map((row) => ({ uuid: row.workflow_uuid as string, name: row.name })),
            );
        };

        void load();
    }, [authLoading, user]);

    // An agent handing to itself is a call that never reaches anybody. The
    // backend refuses the circle either way; not offering it is the difference
    // between a rule and a trap.
    const options = (agents ?? []).filter((agent) => agent.uuid !== excludeUuid);

    if (agents !== null && options.length === 0) {
        return (
            <p className="text-xs text-muted-foreground">
                You have no other agents yet. Build a second one and it can take
                the call from here.
            </p>
        );
    }

    return (
        <Select value={value || undefined} onValueChange={onChange}>
            <SelectTrigger>
                <SelectValue
                    placeholder={agents === null ? "Loading…" : "Choose an agent"}
                />
            </SelectTrigger>
            <SelectContent>
                {options.map((agent) => (
                    <SelectItem key={agent.uuid} value={agent.uuid}>
                        {agent.name}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}
