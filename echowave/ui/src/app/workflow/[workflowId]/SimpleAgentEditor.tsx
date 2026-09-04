"use client";

import { GitBranch, Info } from "lucide-react";
import { useMemo } from "react";

import { readSimpleAgent, writeSimpleAgent } from "@/components/flow/simpleAgent";
import type { FlowNode } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/**
 * A one-prompt agent, as one screen.
 *
 * Modelled on Vapi's assistant editor, which is what a first-time author can
 * actually read: what it says first, and what it is trying to do. Everything
 * else on that screen is a control, not a concept.
 *
 * Decibyl keeps the shared persona in its own node rather than folding it into
 * the prompt the way Vapi does, so it appears here as its own field. Merging
 * the two into one box would be tidier and would quietly change what the agent
 * is: the persona applies to every node, and on the day this agent grows a
 * second one that difference is the whole point of the field.
 *
 * The canvas is never hidden — it is one button away, and the moment this agent
 * grows a branch or a second agent the form stops being offered at all, because
 * a form cannot show a decision.
 */

const PROMPT_ROWS = 16;

export function SimpleAgentEditor({
    nodes,
    onNodesChange,
    onOpenCanvas,
    readOnly = false,
}: {
    nodes: FlowNode[];
    onNodesChange: (next: FlowNode[]) => void;
    onOpenCanvas: () => void;
    readOnly?: boolean;
}) {
    const fields = useMemo(() => readSimpleAgent(nodes), [nodes]);

    const update = (patch: Parameters<typeof writeSimpleAgent>[1]) => {
        if (readOnly) return;
        onNodesChange(writeSimpleAgent(nodes, patch));
    };

    // Characters, not tokens. A token count needs the tokeniser for whichever
    // model this agent resolves to, and a number that is wrong in a way nobody
    // can see is worse than a number that is honest about what it counts.
    const promptChars = fields.systemPrompt.length;

    return (
        <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6">
            <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h2 className="text-lg font-semibold">This agent</h2>
                    <p className="text-sm text-muted-foreground">
                        One agent, one job. It answers, it does the thing, it hangs up.
                    </p>
                </div>
                <Button variant="outline" size="sm" onClick={onOpenCanvas}>
                    <GitBranch className="mr-2 h-4 w-4" />
                    Open canvas
                </Button>
            </div>

            <div className="space-y-6">
                <div className="space-y-2">
                    <Label htmlFor="simple-first-message">First message</Label>
                    <Textarea
                        id="simple-first-message"
                        rows={2}
                        value={fields.firstMessage}
                        readOnly={readOnly}
                        placeholder="Hello, thanks for calling Sunrise Clinic."
                        onChange={(e) => update({ firstMessage: e.target.value })}
                    />
                    <p className="text-xs text-muted-foreground">
                        What it says before the caller has said anything. Leave it empty
                        and the agent waits for them to speak first.
                    </p>
                </div>

                <div className="space-y-2">
                    <div className="flex items-baseline justify-between gap-3">
                        <Label htmlFor="simple-system-prompt">System prompt</Label>
                        <span className="text-xs tabular-nums text-muted-foreground">
                            {promptChars.toLocaleString()} characters
                        </span>
                    </div>
                    <Textarea
                        id="simple-system-prompt"
                        rows={PROMPT_ROWS}
                        value={fields.systemPrompt}
                        readOnly={readOnly}
                        placeholder="Find out what the caller needs, answer it, and book them in if they want an appointment."
                        onChange={(e) => update({ systemPrompt: e.target.value })}
                        className="font-mono text-sm leading-relaxed"
                    />
                    <p className="text-xs text-muted-foreground">
                        Brief it the way you would brief a new receptionist on their first
                        morning: what the job is, what to ask, and where to stop.
                    </p>
                </div>

                <div className="space-y-2">
                    <Label htmlFor="simple-persona">Persona</Label>
                    <Textarea
                        id="simple-persona"
                        rows={4}
                        value={fields.persona}
                        readOnly={readOnly}
                        placeholder="Warm, brief, and never pushy."
                        onChange={(e) => update({ persona: e.target.value })}
                    />
                    <p className="text-xs text-muted-foreground">
                        How it sounds, applied on top of the prompt above. Kept separate
                        because it carries over to every agent you add later.
                    </p>
                </div>

                {(fields.toolUuids.length > 0 || fields.documentUuids.length > 0) && (
                    <p className="flex items-start gap-2 rounded-lg border border-border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                        <Info className="mt-0.5 h-4 w-4 shrink-0" />
                        <span>
                            This agent uses{" "}
                            {fields.toolUuids.length > 0 && (
                                <strong>
                                    {fields.toolUuids.length} skill
                                    {fields.toolUuids.length === 1 ? "" : "s"}
                                </strong>
                            )}
                            {fields.toolUuids.length > 0 &&
                                fields.documentUuids.length > 0 &&
                                " and "}
                            {fields.documentUuids.length > 0 && (
                                <strong>
                                    {fields.documentUuids.length} document
                                    {fields.documentUuids.length === 1 ? "" : "s"}
                                </strong>
                            )}
                            . Change them on the canvas.
                        </span>
                    </p>
                )}
            </div>
        </div>
    );
}
