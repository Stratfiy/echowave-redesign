"use client";

import { ArrowRightLeft, GitBranch, Info, PhoneOff, Play } from "lucide-react";
import { useMemo, useState } from "react";

import { readFlowAgent, writeFlowAgent } from "@/components/flow/flowAgent";
import type { FlowEdge, FlowNode } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

/**
 * A multi-step agent, as one screen.
 *
 * The screen an agent opens on when it is more than one prompt: what it says
 * first, the rules it runs under, and each step's instruction in the order a
 * call meets them. What it does not show is the branching — which step
 * follows which, and on what condition — because a form cannot draw a
 * decision. That stays on the canvas, one button away, and the header says
 * so rather than letting anybody believe this is the whole agent.
 *
 * Every prompt is editable here. Adding a step, an edge or a tool is canvas
 * work; changing what a step says is not, and it is what people open an agent
 * to do.
 */
export function FlowAgentEditor({
    nodes,
    edges,
    onNodesChange,
    onOpenCanvas,
    readOnly = false,
    header,
}: {
    nodes: FlowNode[];
    edges: FlowEdge[];
    onNodesChange: (next: FlowNode[]) => void;
    onOpenCanvas: () => void;
    readOnly?: boolean;
    header?: React.ReactNode;
}) {
    const fields = useMemo(() => readFlowAgent(nodes, edges), [nodes, edges]);
    const [open, setOpen] = useState<string | null>(fields.steps[0]?.id ?? null);

    const update = (patch: Parameters<typeof writeFlowAgent>[1]) => {
        if (readOnly) return;
        onNodesChange(writeFlowAgent(nodes, patch));
    };

    const handoffs = fields.steps.filter((s) => s.type === "handoff").length;

    return (
        <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6">
            {header && <div className="mb-8">{header}</div>}

            <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h2 className="text-lg font-semibold">
                        {handoffs > 0 ? "This squad" : "This agent"}
                    </h2>
                    <p className="text-sm text-muted-foreground">
                        {fields.steps.length} steps
                        {handoffs > 0 && `, ${handoffs} handed to other agents`}. How the
                        call moves between them is on the canvas.
                    </p>
                </div>
                <Button variant="outline" size="sm" onClick={onOpenCanvas}>
                    <GitBranch className="mr-2 h-4 w-4" />
                    Open canvas
                </Button>
            </div>

            <div className="space-y-6">
                <div className="space-y-2">
                    <Label htmlFor="flow-first-message">First message</Label>
                    <Textarea
                        id="flow-first-message"
                        rows={2}
                        value={fields.firstMessage}
                        readOnly={readOnly}
                        placeholder="Namaste, City Clinic. How may I help you today?"
                        onChange={(e) => update({ firstMessage: e.target.value })}
                    />
                    <p className="text-xs text-muted-foreground">
                        What it says before the caller has said anything.
                    </p>
                </div>

                <div className="space-y-2">
                    <Label htmlFor="flow-persona">Rules and persona</Label>
                    <Textarea
                        id="flow-persona"
                        rows={5}
                        value={fields.persona}
                        readOnly={readOnly}
                        placeholder="Warm, brief, and never pushy. Follow the caller's language."
                        onChange={(e) => update({ persona: e.target.value })}
                        className="font-mono text-sm leading-relaxed"
                    />
                    <p className="text-xs text-muted-foreground">
                        Applies on every step of the call, on top of each step&apos;s own
                        instruction.
                    </p>
                </div>

                <div className="space-y-2">
                    <p className="text-sm font-medium">Steps</p>
                    <ol className="divide-y divide-border rounded-xl border border-border">
                        {fields.steps.map((step, index) => {
                            const expanded = open === step.id;
                            const Icon =
                                step.type === "handoff"
                                    ? ArrowRightLeft
                                    : step.type === "endCall"
                                      ? PhoneOff
                                      : Play;
                            return (
                                <li key={step.id}>
                                    <button
                                        type="button"
                                        aria-expanded={expanded}
                                        aria-controls={`flow-step-${step.id}`}
                                        onClick={() => setOpen(expanded ? null : step.id)}
                                        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/40"
                                    >
                                        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold tabular-nums">
                                            {index + 1}
                                        </span>
                                        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                                        <span className="min-w-0 flex-1">
                                            <span className="block truncate font-medium">
                                                {step.name || `Step ${index + 1}`}
                                            </span>
                                            {!expanded && (
                                                <span className="block truncate text-xs text-muted-foreground">
                                                    {step.type === "handoff"
                                                        ? "Hands the call to another agent"
                                                        : step.prompt.split("\n")[0] || "No instruction yet"}
                                                </span>
                                            )}
                                        </span>
                                        {(step.toolCount > 0 || step.documentCount > 0) && (
                                            <span className="shrink-0 text-xs text-muted-foreground">
                                                {step.toolCount > 0 && `${step.toolCount} skill${step.toolCount === 1 ? "" : "s"}`}
                                                {step.toolCount > 0 && step.documentCount > 0 && " · "}
                                                {step.documentCount > 0 && `${step.documentCount} doc${step.documentCount === 1 ? "" : "s"}`}
                                            </span>
                                        )}
                                    </button>
                                    <div
                                        id={`flow-step-${step.id}`}
                                        hidden={!expanded}
                                        className={cn("px-4 pb-4", expanded && "border-t border-border pt-3")}
                                    >
                                        {step.type === "handoff" ? (
                                            <p className="text-sm text-muted-foreground">
                                                From here the call continues with another of your
                                                agents and does not come back. Which one is chosen on
                                                the canvas.
                                            </p>
                                        ) : (
                                            <>
                                                <Label htmlFor={`flow-step-prompt-${step.id}`} className="sr-only">
                                                    Instruction for {step.name}
                                                </Label>
                                                <Textarea
                                                    id={`flow-step-prompt-${step.id}`}
                                                    rows={10}
                                                    value={step.prompt}
                                                    readOnly={readOnly}
                                                    onChange={(e) =>
                                                        update({ step: { id: step.id, prompt: e.target.value } })
                                                    }
                                                    className="font-mono text-sm leading-relaxed"
                                                />
                                            </>
                                        )}
                                    </div>
                                </li>
                            );
                        })}
                    </ol>
                </div>

                <p className="flex items-start gap-2 rounded-lg border border-border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                    <Info className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>
                        To add a step, change when the call moves between steps, or attach a
                        skill or document, open the canvas.
                    </span>
                </p>
            </div>
        </div>
    );
}

export default FlowAgentEditor;
