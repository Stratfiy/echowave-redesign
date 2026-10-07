"use client";

/**
 * The advanced workflow on a phone (screen 45): a readable list of steps in
 * the order a run takes them, with the agent's status, instead of a canvas
 * shrunk until nothing on it can be touched. Read-only by design -- complex
 * graph edits stay in the editor on a larger screen, and saving a layout
 * here could never publish a changed workflow because nothing here saves.
 */

import { AlertTriangle, ExternalLink, Monitor } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";

type Node = { id: string; type?: string; data?: { name?: string; prompt?: string; invalid?: boolean; validationMessage?: string | null; is_start?: boolean } };
type Edge = { source: string; target: string; data?: { label?: string; condition?: string } };

const KIND: Record<string, string> = {
    startCall: "Start",
    agentNode: "Step",
    endCall: "End",
    globalNode: "Applies everywhere",
    trigger: "Trigger",
    webhook: "Webhook",
    qa: "Quality check",
};

/** Steps in the order a run meets them: from the start, breadth first, then
 *  anything unreachable at the end (listed, never dropped). */
export function orderSteps(nodes: readonly Node[], edges: readonly Edge[]): Node[] {
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const start = nodes.find((n) => n.type === "startCall" || n.data?.is_start) ?? nodes[0];
    const seen = new Set<string>();
    const ordered: Node[] = [];
    const queue = start ? [start.id] : [];
    while (queue.length) {
        const id = queue.shift()!;
        if (seen.has(id) || !byId.has(id)) continue;
        seen.add(id);
        ordered.push(byId.get(id)!);
        for (const edge of edges) if (edge.source === id && !seen.has(edge.target)) queue.push(edge.target);
    }
    for (const node of nodes) if (!seen.has(node.id)) ordered.push(node);
    return ordered;
}

export function ReadOnlyStepList({
    workflowId,
    name,
    nodes,
    edges,
    versionStatus,
    totalRuns,
    onOpenEditor,
}: {
    workflowId: number;
    name: string;
    nodes: readonly Node[];
    edges: readonly Edge[];
    versionStatus?: string | null;
    totalRuns?: number;
    /** Open the full editor anyway, for someone who knows what they want. */
    onOpenEditor?: () => void;
}) {
    const steps = orderSteps(nodes, edges);
    const nextOf = (id: string) => edges.filter((e) => e.source === id).map((e) => e.target);
    const nameOf = (id: string) => nodes.find((n) => n.id === id)?.data?.name || "a step";
    return (
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-4 py-4" data-testid="read-only-steps">
            <header className="flex flex-col gap-1">
                <h1 className="break-words text-xl font-semibold">{name}</h1>
                <p className="text-sm text-muted-foreground">
                    {versionStatus ? `${versionStatus[0].toUpperCase()}${versionStatus.slice(1)} version` : "Current version"}
                    {typeof totalRuns === "number" ? ` · ${totalRuns} ${totalRuns === 1 ? "run" : "runs"}` : ""}
                </p>
            </header>
            <p className="flex items-start gap-2 rounded-[var(--radius)] border border-border bg-muted/30 p-3 text-sm">
                <Monitor aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                <span>You are viewing the steps. Editing the flow needs a larger screen.</span>
            </p>
            <ol className="flex flex-col gap-2" aria-label="Steps">
                {steps.map((step, index) => {
                    const after = nextOf(step.id);
                    return (
                        <li key={step.id} className="rounded-[var(--radius)] border border-border p-3" data-testid="step">
                            <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
                                <span className="text-xs text-muted-foreground">{index + 1}.</span>
                                <span className="font-medium">{step.data?.name || KIND[step.type ?? ""] || "Step"}</span>
                                <span className="text-xs text-muted-foreground">{KIND[step.type ?? ""] ?? step.type}</span>
                            </p>
                            {step.data?.prompt && <p className="mt-1 line-clamp-3 break-words text-sm text-muted-foreground">{step.data.prompt}</p>}
                            {step.data?.invalid && (
                                <p className="mt-1 flex items-center gap-1 text-xs text-[#772322] dark:text-red-300">
                                    <AlertTriangle aria-hidden className="h-3.5 w-3.5" />
                                    {step.data.validationMessage || "This step needs fixing in the editor."}
                                </p>
                            )}
                            {after.length > 0 && (
                                <p className="mt-1 text-xs text-muted-foreground">Then: {after.map(nameOf).join(", ")}</p>
                            )}
                        </li>
                    );
                })}
            </ol>
            <div className="flex flex-wrap gap-2">
                <Button asChild variant="outline" className="motion-m1 min-h-11">
                    <Link href={`/workflow/${workflowId}/runs`}>
                        Runs <ExternalLink aria-hidden />
                    </Link>
                </Button>
                {onOpenEditor && (
                    <Button type="button" variant="ghost" className="motion-m1 min-h-11" onClick={onOpenEditor}>
                        Open the editor anyway
                    </Button>
                )}
            </div>
        </div>
    );
}

export default ReadOnlyStepList;
