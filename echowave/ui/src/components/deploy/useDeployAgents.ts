"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { getWorkflowsApiV1WorkflowFetchGet } from "@/client/sdk.gen";

export type DeployAgent = { id: number; name: string; uuid: string | null };

/**
 * The agent picker's data, shared by every screen under DEPLOY.
 *
 * Both deploy screens ask the same three questions — which agents exist, which
 * one is selected, and what does `?agent=` say — and the second copy of that
 * would be the one that stops honouring the query parameter.
 */
export function useDeployAgents(requestedAgent: string | null) {
    const [agents, setAgents] = useState<DeployAgent[] | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [selectedId, setSelectedId] = useState<number | null>(null);

    const load = useCallback(async () => {
        try {
            const response = await getWorkflowsApiV1WorkflowFetchGet({
                query: { status: "active" },
            });
            const data = response.data
                ? Array.isArray(response.data)
                    ? response.data
                    : [response.data]
                : [];
            setAgents(
                data.map((w) => ({
                    id: w.id as number,
                    name: w.name as string,
                    uuid: (w.workflow_uuid as string | null) ?? null,
                })),
            );
        } catch {
            // An empty picker after a network failure reads as "you have no
            // agents", which is a different and far more alarming thing than
            // "we could not reach the server".
            setLoadError("Could not load your agents. Reload to try again.");
            setAgents([]);
        }
    }, []);

    // `?agent=` wins on first load, so a link from an agent's settings screen
    // lands on that agent rather than on whichever happens to be first.
    useEffect(() => {
        if (!agents || agents.length === 0) return;
        setSelectedId((current) => {
            if (current !== null && agents.some((a) => a.id === current)) return current;
            const asked = requestedAgent ? Number(requestedAgent) : NaN;
            if (Number.isFinite(asked) && agents.some((a) => a.id === asked)) return asked;
            return agents[0].id;
        });
    }, [agents, requestedAgent]);

    const selected = useMemo(
        () => agents?.find((a) => a.id === selectedId) ?? null,
        [agents, selectedId],
    );

    return { agents, selected, selectedId, setSelectedId, loadError, load };
}
