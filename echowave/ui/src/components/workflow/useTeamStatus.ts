"use client";

/**
 * What every agent is doing, keyed by workflow id: the sentence under its
 * name, its tone, its last line and the last day's numbers (routes/team.py).
 *
 * One request for the whole directory, not one per card. A failure returns
 * an empty map: the cards still render from the workflow list, they just say
 * less. Losing the roster must not lose the agents.
 */

import { useEffect, useState } from "react";

import { teamStatusApiV1TeamStatusGet } from "@/client/sdk.gen";
import type { TeamMember } from "@/client/types.gen";
import { useAuth } from "@/lib/auth";

export type Tone = "attention" | "working" | "idle" | "paused";

export function useTeamStatus(): Record<number, TeamMember> {
    const { user, loading } = useAuth();
    const [byWorkflow, setByWorkflow] = useState<Record<number, TeamMember>>({});

    useEffect(() => {
        if (loading || !user) return;
        let cancelled = false;
        (async () => {
            try {
                const response = await teamStatusApiV1TeamStatusGet({ query: { hours: 24 } });
                if (cancelled || response.error) return;
                const next: Record<number, TeamMember> = {};
                for (const member of response.data?.members ?? []) {
                    next[member.workflow_id] = member;
                }
                setByWorkflow(next);
            } catch {
                /* see the docstring: the cards stand without it */
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [loading, user]);

    return byWorkflow;
}

/** The tone an agent is in, from the roster or, without it, its own switch. */
export function toneOf(member: TeamMember | undefined, isLive: boolean | undefined): Tone {
    const tone = member?.tone;
    if (tone === "attention" || tone === "working" || tone === "idle" || tone === "paused") {
        return tone;
    }
    return isLive === false ? "paused" : "idle";
}
