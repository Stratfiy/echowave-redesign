/**
 * Which skills this agent may reach for.
 *
 * Tools were org-level objects with no per-agent view: you created one on
 * `/tools`, then attached it by opening the canvas, clicking the agent node,
 * and finding a picker inside it. For a one-prompt agent that is three steps to
 * answer "can this agent book an appointment", and the canvas is the screen we
 * spent yesterday arguing that a simple agent should not have to open.
 *
 * So the same list, on the agent, as a tab.
 *
 * **Only for an agent that is one agent.** A graph names its tools per node —
 * the booking step gets the calendar, the payment step does not — and one flat
 * list here would have to either lie about that or silently write the same set
 * to every node. It says so and points at the canvas instead, which is the
 * screen that can actually express it.
 */

"use client";

import { ArrowLeft, Loader2, Wrench } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AgentTabs } from "@/app/workflow/[workflowId]/components/AgentTabs";
import {
    getWorkflowApiV1WorkflowFetchWorkflowIdGet,
    listToolsApiV1ToolsGet,
    updateWorkflowApiV1WorkflowWorkflowIdPut,
} from "@/client/sdk.gen";
import { isSimpleAgent, readSimpleAgent, writeSimpleAgent } from "@/components/flow/simpleAgent";
import type { FlowEdge, FlowNode } from "@/components/flow/types";
import SpinLoader from "@/components/SpinLoader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

import WorkflowLayout from "../../WorkflowLayout";

type Tool = {
    tool_uuid: string;
    name: string;
    description?: string | null;
    status?: string | null;
};

export default function AgentToolsPage() {
    const params = useParams();
    const router = useRouter();
    const workflowId = Number(params.workflowId);
    const { user, loading: authLoading, redirectToLogin } = useAuth();
    const hasFetched = useRef(false);

    const [name, setName] = useState("");
    const [nodes, setNodes] = useState<FlowNode[]>([]);
    const [edges, setEdges] = useState<FlowEdge[]>([]);
    const [tools, setTools] = useState<Tool[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        if (!authLoading && !user) redirectToLogin();
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;

        const load = async () => {
            const [workflowResult, toolsResult] = await Promise.all([
                getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                    path: { workflow_id: workflowId },
                }),
                listToolsApiV1ToolsGet({ query: { status: "active" } }),
            ]);

            if (workflowResult.error) {
                setError(detailFromResult(workflowResult, "Could not load this agent"));
                setLoading(false);
                return;
            }
            const flow = workflowResult.data?.workflow_definition as
                | { nodes?: FlowNode[]; edges?: FlowEdge[] }
                | undefined;
            setName(workflowResult.data?.name ?? "");
            setNodes(flow?.nodes ?? []);
            setEdges(flow?.edges ?? []);
            // A failure here is not fatal: the agent still renders, the list is
            // simply empty and says so.
            if (!toolsResult.error) setTools((toolsResult.data as Tool[]) ?? []);
            setLoading(false);
        };

        load();
    }, [authLoading, user, workflowId]);

    const simple = useMemo(() => isSimpleAgent(nodes, edges), [nodes, edges]);
    const attached = useMemo(
        () => new Set(simple ? readSimpleAgent(nodes).toolUuids : []),
        [simple, nodes],
    );

    const toggle = useCallback(
        async (uuid: string, on: boolean) => {
            const next = new Set(attached);
            if (on) next.add(uuid);
            else next.delete(uuid);

            const updated = writeSimpleAgent(nodes, { toolUuids: [...next] });
            setNodes(updated);
            setSaving(true);
            setError(null);
            setSaved(false);
            try {
                const result = await updateWorkflowApiV1WorkflowWorkflowIdPut({
                    path: { workflow_id: workflowId },
                    body: {
                        name,
                        // The viewport is the canvas's business. Omitting it
                        // leaves whatever was stored rather than resetting
                        // somebody's pan and zoom from a screen that has no
                        // canvas on it.
                        workflow_definition: { nodes: updated, edges },
                    },
                });
                if (result.error) {
                    setError(detailFromResult(result, "Could not save the change"));
                    // Put the toggle back: a checkbox that stays ticked after a
                    // failed save is the screen telling a lie it can be quoted
                    // on later.
                    setNodes(nodes);
                    return;
                }
                setSaved(true);
            } catch {
                setError("Could not reach the server. Try again.");
                setNodes(nodes);
            } finally {
                setSaving(false);
            }
        },
        [attached, nodes, edges, name, workflowId],
    );

    if (authLoading || loading) return <SpinLoader />;

    return (
        <WorkflowLayout>
            <div className="flex h-14 items-center gap-3 border-b bg-[#1a1a1a] px-4">
                <button
                    onClick={() => router.push(`/workflow/${workflowId}`)}
                    className="flex h-8 w-8 items-center justify-center rounded-lg transition-colors hover:bg-[#2a2a2a]"
                    aria-label="Back to agent"
                >
                    <ArrowLeft className="h-5 w-5 text-gray-400" />
                </button>
                <p className="truncate text-sm font-semibold text-white">{name}</p>
            </div>

            <AgentTabs workflowId={workflowId} />

            <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6">
                <h2 className="text-lg font-semibold">Tools</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    What this agent can do besides talk — book, look up, transfer.
                    It decides when to use one from the description, so the
                    description is the instruction.
                </p>

                {error && (
                    <div className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {error}
                    </div>
                )}

                {!simple ? (
                    <Card className="mt-6">
                        <CardContent className="p-5">
                            <p className="text-sm">
                                This agent has more than one step, and each step
                                chooses its own tools — the booking step needs a
                                calendar, the closing step does not.
                            </p>
                            <p className="mt-2 text-sm text-muted-foreground">
                                Open the canvas and pick a step to set its tools.
                            </p>
                            <Button
                                variant="outline"
                                className="mt-4"
                                onClick={() => router.push(`/workflow/${workflowId}`)}
                            >
                                Open canvas
                            </Button>
                        </CardContent>
                    </Card>
                ) : tools.length === 0 ? (
                    <Card className="mt-6">
                        <CardContent className="p-5">
                            <p className="text-sm">No tools on this account yet.</p>
                            <p className="mt-2 text-sm text-muted-foreground">
                                A tool is something the agent can call mid-call —
                                checking an order, booking a slot, sending an SMS.
                            </p>
                            <Button
                                variant="outline"
                                className="mt-4"
                                onClick={() => router.push("/tools")}
                            >
                                Create a tool
                            </Button>
                        </CardContent>
                    </Card>
                ) : (
                    <>
                        <div className="mt-6 space-y-2">
                            {tools.map((tool) => (
                                <label
                                    key={tool.tool_uuid}
                                    className="flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition-colors hover:bg-muted/40"
                                >
                                    <Checkbox
                                        checked={attached.has(tool.tool_uuid)}
                                        disabled={saving}
                                        onCheckedChange={(checked) =>
                                            toggle(tool.tool_uuid, checked === true)
                                        }
                                        className="mt-0.5"
                                    />
                                    <span className="min-w-0 flex-1">
                                        <span className="flex items-center gap-2 font-medium">
                                            <Wrench className="h-3.5 w-3.5 text-muted-foreground" />
                                            {tool.name}
                                        </span>
                                        {tool.description && (
                                            <span className="mt-0.5 block text-sm text-muted-foreground">
                                                {tool.description}
                                            </span>
                                        )}
                                    </span>
                                </label>
                            ))}
                        </div>

                        <p className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
                            {saving ? (
                                <>
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                    Saving…
                                </>
                            ) : saved ? (
                                "Saved."
                            ) : (
                                "Changes save as you tick."
                            )}
                        </p>
                    </>
                )}
            </div>
        </WorkflowLayout>
    );
}
