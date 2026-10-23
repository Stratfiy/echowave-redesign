"use client";

/**
 * "Can see the team": lets this agent read what the other agents in the
 * workspace did, ran, left waiting and spent (the `team_activity` tool,
 * services/workflow/team_activity.py). It is what a chief-of-staff agent
 * needs and nothing else should have by default, so it is off until the
 * owner turns it on here, and the whole card is absent while the feature
 * is switched off for the workspace.
 *
 * Saves on the tick, like the notices above it: one boolean, no Save
 * button to forget. Put back if the server refuses, so the switch never
 * claims access the agent does not have.
 */

import { AlertTriangle, Loader2, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    getTeamAccessApiV1WorkflowWorkflowIdTeamAccessGet,
    setTeamAccessApiV1WorkflowWorkflowIdTeamAccessPut,
} from "@/client/sdk.gen";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { useFeature } from "@/lib/features";

export function TeamAccessCard({ workflowId }: { workflowId: number }) {
    const available = useFeature("team_activity");
    const [enabled, setEnabled] = useState(false);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!available) return;
        let cancelled = false;
        (async () => {
            const response = await getTeamAccessApiV1WorkflowWorkflowIdTeamAccessGet({
                path: { workflow_id: workflowId },
            });
            if (cancelled) return;
            if (response.error || !response.data) {
                setError("Could not read whether this agent can see the team.");
            } else {
                setEnabled(Boolean(response.data.enabled));
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [available, workflowId]);

    const toggle = useCallback(
        async (on: boolean) => {
            const before = enabled;
            setEnabled(on);
            setSaving(true);
            setError(null);
            const response = await setTeamAccessApiV1WorkflowWorkflowIdTeamAccessPut({
                path: { workflow_id: workflowId },
                body: { enabled: on },
            });
            if (response.error) {
                setEnabled(before);
                setError("That did not save. Try again.");
            }
            setSaving(false);
        },
        [enabled, workflowId],
    );

    if (!available) return null;

    return (
        <Card id="team-access">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Users className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                    Can see the team
                </CardTitle>
                <CardDescription>
                    Lets this agent read what the other agents in your workspace did:
                    their runs, files they drafted, approvals waiting and what they
                    spent. It only reads. Off unless you turn it on.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-1">
                {loading && (
                    <p className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                        Reading…
                    </p>
                )}

                {error && (
                    <p
                        className="flex items-center gap-2 py-2 text-sm text-destructive"
                        role="alert"
                    >
                        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
                        {error}
                    </p>
                )}

                {!loading && (
                    <label className="flex cursor-pointer items-start justify-between gap-4 rounded-lg px-2 py-2.5 transition-colors hover:bg-muted/40">
                        <span className="min-w-0">
                            <span className="block text-sm font-medium">
                                Can see the team
                            </span>
                            <span className="block text-sm text-muted-foreground">
                                Chat and scheduled runs only, not phone calls.
                            </span>
                        </span>
                        <Switch
                            checked={enabled}
                            disabled={saving}
                            onCheckedChange={(on) => void toggle(on)}
                            aria-label="Can see the team"
                        />
                    </label>
                )}
            </CardContent>
        </Card>
    );
}

export default TeamAccessCard;
