/**
 * Whether this agent's calls get reviewed, and a switch to say so.
 *
 * QA has been fully built for a long time — `services/workflow/qa/analysis.py`
 * scores a call against a prompt, splits it per node, and writes the result to
 * `workflow_runs.annotations`, which the call detail page already renders.
 *
 * It also, for that whole time, ran on almost nothing: `run_integrations` looks
 * for `nodes` of type `"qa"` and no creation path made one. Every creation path
 * now does (`services/workflow/qa_node.py`), so a new agent arrives with review
 * already on and this switch reads as on rather than introducing it.
 *
 * The switch stays per agent, because that is the level the cost sits at — a
 * review is an extra LLM call per call — and an account with one high-volume
 * agent and five low-volume ones has a real reason to want it off on exactly
 * one of them. It also remains the only control for agents created before
 * review was made a default.
 *
 * Off keeps the node and clears `qa_enabled` rather than deleting it, so any
 * extractions or prompt configured on it survive being switched off and on.
 */

"use client";

import { ClipboardCheck, ExternalLink, Loader2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    getWorkflowApiV1WorkflowFetchWorkflowIdGet,
    updateWorkflowApiV1WorkflowWorkflowIdPut,
} from "@/client/sdk.gen";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

/** What the runtime looks for: a node of this type, edges irrelevant. */
const QA_NODE_TYPE = "qa";

type GraphNode = {
    id: string;
    type?: string;
    position?: { x: number; y: number };
    data?: Record<string, unknown>;
};

export function QaCard({ workflowId }: { workflowId: number }) {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    const [name, setName] = useState("");
    const [nodes, setNodes] = useState<GraphNode[]>([]);
    const [edges, setEdges] = useState<unknown[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const result = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({
            path: { workflow_id: workflowId },
        });
        if (result.error) {
            setError(detailFromResult(result, "Could not load this agent"));
            setLoading(false);
            return;
        }
        const flow = result.data?.workflow_definition as
            | { nodes?: GraphNode[]; edges?: unknown[] }
            | undefined;
        setName(result.data?.name ?? "");
        setNodes(flow?.nodes ?? []);
        setEdges(flow?.edges ?? []);
        setLoading(false);
    }, [workflowId]);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        load();
    }, [authLoading, user, load]);

    const qaNode = nodes.find((n) => n.type === QA_NODE_TYPE);
    const enabled = Boolean(qaNode && qaNode.data?.qa_enabled !== false);

    const toggle = useCallback(
        async (on: boolean) => {
            setSaving(true);
            setError(null);

            let next: GraphNode[];
            if (qaNode) {
                next = nodes.map((n) =>
                    n === qaNode
                        ? { ...n, data: { ...(n.data ?? {}), qa_enabled: on } }
                        : n,
                );
            } else {
                // Placed clear of the conversation nodes rather than at the
                // origin, where it would land on top of the start node for
                // anyone who does open the canvas. It has no edges: the
                // runtime finds it by type after the call, not by walking the
                // graph during one.
                next = [
                    ...nodes,
                    {
                        id: `qa-${Date.now()}`,
                        type: QA_NODE_TYPE,
                        position: { x: 80, y: 520 },
                        data: { name: "Call review", qa_enabled: on },
                    },
                ];
            }

            const result = await updateWorkflowApiV1WorkflowWorkflowIdPut({
                path: { workflow_id: workflowId },
                body: {
                    name,
                    workflow_definition: { nodes: next, edges },
                },
            });
            if (result.error) {
                setError(detailFromResult(result, "Could not change call review"));
                setSaving(false);
                return;
            }
            setNodes(next);
            setSaving(false);
        },
        [nodes, edges, qaNode, name, workflowId],
    );

    if (loading || !user) return null;

    return (
        <Card id="qa">
            <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                <div>
                    <CardTitle className="flex items-center gap-2 text-base">
                        <ClipboardCheck className="h-4 w-4" />
                        Call review
                    </CardTitle>
                    <CardDescription>
                        Scores each finished call against what you asked for, and
                        tags what happened. The review runs once per step the
                        conversation actually reached, on the same model the
                        agent uses, and those tokens appear on your bill like the
                        call&apos;s own. Calls under 15 seconds are skipped.
                    </CardDescription>
                </div>
                <Switch
                    checked={enabled}
                    disabled={saving}
                    onCheckedChange={toggle}
                    aria-label="Review calls for this agent"
                />
            </CardHeader>
            <CardContent className="space-y-3">
                {error && (
                    <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                        {error}
                    </p>
                )}

                {saving ? (
                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        Saving…
                    </p>
                ) : enabled ? (
                    <p className="text-sm text-muted-foreground">
                        On. Calls from here on get reviewed — the score and tags
                        appear on each call under{" "}
                        <Link
                            href={`/workflow/${workflowId}/runs`}
                            className="inline-flex items-center gap-0.5 underline underline-offset-2"
                        >
                            Logs <ExternalLink className="h-3 w-3" />
                        </Link>
                        . Calls already made are not reviewed retrospectively.
                    </p>
                ) : (
                    <p className="text-sm text-muted-foreground">
                        Off, so nothing is being reviewed and this agent&apos;s
                        calls carry no score. That is why Analysis looks empty.
                    </p>
                )}
            </CardContent>
        </Card>
    );
}
