"use client";

/**
 * The huddle, docked in the agent's thread: talk to the agent as a teammate.
 *
 * It answers about its own work and proposes changes; a change is a card in
 * the thread beside this panel, and nothing is published by voice. "Try it
 * as a customer" swaps the panel to the agent's real test call (the same
 * tester the editor uses), in place -- nobody is sent to another screen.
 *
 * One microphone at a time: switching to the customer test ends the huddle
 * first, and the test starts only when asked.
 *
 * While the agent is on a live call, the huddle is that call's whisper
 * channel, marked "This call only" (`HuddleLiveCall`, with live_supervision).
 */

import { Mic, MicOff, Phone, PhoneOff, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { WorkflowTesterPanel } from "@/app/workflow/[workflowId]/components/WorkflowTesterPanel";
import { forgetHuddleNotesApiV1HuddleWorkflowIdNotesDelete, huddleNotesApiV1HuddleWorkflowIdNotesGet } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { ACTIVE, FINAL, LABEL, type VoicePhase } from "@/lib/voice/sessionState";

import { HuddleLiveCall } from "./HuddleLiveCall";
import type { Huddle } from "./useHuddle";

/** Phases where the microphone is capturing, so the meter is real. */
const CAPTURING = new Set<VoicePhase>(["listening", "processing", "speaking"]);

export function huddleLabel(phase: VoicePhase, agentName: string): string {
    if (phase === "speaking") return `${agentName} is speaking`;
    if (phase === "idle") return "Not started";
    return LABEL[phase];
}

export function HuddlePanel({
    workflowId,
    agentName,
    chatOnly,
    huddle,
    notesVersion = 0,
}: {
    workflowId: number;
    agentName: string;
    /** A chat agent's customer test is a text chat, not a call. */
    chatOnly: boolean;
    huddle: Huddle;
    /** Bumped when the agent keeps a note, so the list is read again. */
    notesVersion?: number;
}) {
    const { user, loading: authLoading } = useAuth();
    const { state, inputLevel } = huddle;
    const [asCustomer, setAsCustomer] = useState(false);
    const [notes, setNotes] = useState<string[]>([]);
    const [notesError, setNotesError] = useState<string | null>(null);
    const live = ACTIVE.has(state.phase);
    const final = FINAL.has(state.phase);
    const capturing = CAPTURING.has(state.phase) && !state.muted;
    const started = useRef(false);
    const captionsEnd = useRef<HTMLDivElement | null>(null);

    // One tap on the call button is the huddle starting: no second button.
    useEffect(() => {
        if (started.current || asCustomer) return;
        started.current = true;
        if (state.phase === "idle") void huddle.start({ threadId: null, draft: "" });
        // Once, when the panel opens.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        captionsEnd.current?.scrollIntoView?.({ block: "end" });
    }, [state.captions]);

    const loadNotes = useCallback(async () => {
        const response = await huddleNotesApiV1HuddleWorkflowIdNotesGet({ path: { workflow_id: workflowId } });
        if (response.error) {
            setNotesError(detailFromError(response.error, "Could not read the notes."));
            return;
        }
        setNotesError(null);
        setNotes(response.data?.notes ?? []);
    }, [workflowId]);

    useEffect(() => {
        if (authLoading || !user) return;
        void loadNotes();
    }, [authLoading, user, loadNotes, notesVersion]);

    const forget = async () => {
        const response = await forgetHuddleNotesApiV1HuddleWorkflowIdNotesDelete({ path: { workflow_id: workflowId } });
        if (response.error) {
            setNotesError(detailFromError(response.error, "Could not forget the notes."));
            return;
        }
        setNotes([]);
    };

    const switchMode = async (customer: boolean) => {
        if (customer && live) await huddle.end("user_ended", null);
        setAsCustomer(customer);
    };

    const meter = Math.round(Math.min(1, inputLevel * 5) * 100);

    return (
        <div className="flex h-full min-h-0 flex-col" data-testid="huddle-panel" data-phase={state.phase}>
            <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
                <Label htmlFor="huddle-as-customer" className="text-sm">
                    Try it as a customer
                </Label>
                <Switch
                    id="huddle-as-customer"
                    checked={asCustomer}
                    onCheckedChange={(checked) => void switchMode(checked)}
                />
            </div>

            {asCustomer ? (
                <div className="min-h-0 flex-1" data-testid="huddle-customer">
                    <WorkflowTesterPanel
                        workflowId={workflowId}
                        channel={chatOnly ? "chat" : "voice"}
                        disabled={false}
                        disabledReason={null}
                    />
                </div>
            ) : (
                <>
                    <div className="flex flex-col items-center gap-2 px-4 py-5 text-center">
                        <p className="text-lg font-medium" role="status" aria-live="polite" data-testid="huddle-state">
                            {huddleLabel(state.phase, agentName)}
                        </p>
                        {state.notice && (
                            <p className="max-w-sm text-sm text-muted-foreground" data-testid="huddle-notice">
                                {state.notice}
                            </p>
                        )}
                        {capturing ? (
                            <div
                                className="h-1.5 w-40 overflow-hidden rounded-full bg-muted"
                                role="meter"
                                aria-label="Microphone level"
                                aria-valuemin={0}
                                aria-valuemax={100}
                                aria-valuenow={meter}
                            >
                                <div className="h-full rounded-full bg-foreground" style={{ width: `${meter}%` }} />
                            </div>
                        ) : (
                            <div className="h-1.5" aria-hidden />
                        )}
                        {state.phase === "idle" && (
                            <p className="max-w-sm text-sm text-muted-foreground">
                                Ask {agentName} about its calls, its schedule or what it says, and tell it what to
                                change. Changes come back as a card in the thread for you to publish.
                            </p>
                        )}
                    </div>

                    {state.approvalWaiting && (
                        <div
                            className="mx-4 mb-3 rounded-[var(--radius)] border border-border p-3 text-sm"
                            data-testid="huddle-approval"
                        >
                            {agentName} put a change on a card in the thread. Nothing changes until you publish it there.
                        </div>
                    )}

                    <HuddleLiveCall workflowId={workflowId} active={live} />

                    <div className="min-h-0 flex-1 overflow-y-auto px-4" role="log" aria-label="Huddle transcript">
                        {state.captions.map((caption) => (
                            <p
                                key={caption.id}
                                className={cn(
                                    "mb-2 break-words text-sm leading-relaxed",
                                    caption.who === "you" ? "text-right text-muted-foreground" : "text-foreground",
                                )}
                            >
                                <span className="sr-only">{caption.who === "you" ? "You: " : `${agentName}: `}</span>
                                {caption.text}
                            </p>
                        ))}
                        <div ref={captionsEnd} />
                    </div>

                    {(notes.length > 0 || notesError) && (
                        <div className="border-t border-border px-4 py-3 text-sm" data-testid="huddle-notes">
                            {notesError ? (
                                <p className="text-destructive">{notesError}</p>
                            ) : (
                                <>
                                    <div className="mb-1 flex items-center justify-between gap-2">
                                        <p className="font-medium">What {agentName} keeps from your huddles</p>
                                        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => void forget()}>
                                            Forget
                                        </Button>
                                    </div>
                                    <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
                                        {notes.map((note) => (
                                            <li key={note}>{note}</li>
                                        ))}
                                    </ul>
                                </>
                            )}
                        </div>
                    )}

                    <div className="flex flex-wrap items-center justify-center gap-3 border-t border-border px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
                        {live ? (
                            <>
                                <Button
                                    variant="outline"
                                    className="min-h-11"
                                    aria-pressed={state.muted}
                                    onClick={() => void huddle.toggleMute()}
                                >
                                    {state.muted ? <MicOff className="mr-1.5 h-4 w-4" aria-hidden /> : <Mic className="mr-1.5 h-4 w-4" aria-hidden />}
                                    {state.muted ? "Unmute" : "Mute"}
                                </Button>
                                <Button variant="destructive" className="min-h-11" onClick={() => void huddle.end()}>
                                    <PhoneOff className="mr-1.5 h-4 w-4" aria-hidden />
                                    End huddle
                                </Button>
                            </>
                        ) : (
                            <Button
                                className="min-h-11"
                                onClick={() => void huddle.start({ threadId: null, draft: "" })}
                            >
                                {final ? <RotateCcw className="mr-1.5 h-4 w-4" aria-hidden /> : <Phone className="mr-1.5 h-4 w-4" aria-hidden />}
                                {final ? "Start again" : "Start huddle"}
                            </Button>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}

/** A live huddle while its panel is put away: one tap back, End always there. */
export function HuddleStrip({
    agentName,
    huddle,
    onOpen,
}: {
    agentName: string;
    huddle: Huddle;
    onOpen: () => void;
}) {
    const { state } = huddle;
    if (!ACTIVE.has(state.phase)) return null;
    return (
        <div
            className="mx-3 mb-2 flex items-center gap-2 rounded-[var(--radius)] border border-border bg-background p-2 shadow-sm"
            role="region"
            aria-label={`Huddle with ${agentName}`}
            data-testid="huddle-strip"
        >
            <span className="flex min-w-0 flex-1 items-center gap-2 px-1 text-sm">
                {state.muted ? <MicOff aria-hidden className="h-4 w-4 shrink-0" /> : <Mic aria-hidden className="h-4 w-4 shrink-0" />}
                <span className="truncate">
                    Huddle with {agentName} · {huddleLabel(state.phase, agentName)}
                </span>
            </span>
            <Button variant="outline" size="sm" className="min-h-9" onClick={onOpen}>
                Open
            </Button>
            <Button variant="destructive" size="sm" className="min-h-9" onClick={() => void huddle.end()}>
                End
            </Button>
        </div>
    );
}
