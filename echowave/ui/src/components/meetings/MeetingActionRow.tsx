"use client";

/**
 * One suggested follow-up, from suggestion to a task on the board.
 *
 * Before anything happens it shows its source excerpt, owner, task, time,
 * confidence and what is missing (handoff 23). Edit changes owner, task or
 * time; Review puts it on its own action card -- the controls' exact preview,
 * bound to a version -- and only Approve on that card makes the task. After
 * Approve there is a short window to undo; after it runs, the task can be
 * taken back while nobody has started it. Each state reads as a sentence.
 */

import { AlertTriangle, CheckCircle2, Loader2, Quote } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import {
    editActionApiV1MeetingsMeetingIdActionsItemIdPut,
    reviewActionApiV1MeetingsMeetingIdActionsItemIdReviewPost,
    settleActionApiV1MeetingsMeetingIdActionsItemIdSettlePost,
} from "@/client/sdk.gen";
import type { MeetingAction, MeetingRecord } from "@/client/types.gen";
import { ActionPreview, type ApprovalStatus } from "@/components/shell/ActionPreview";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";
import { cardLine, clock, fromLocalInput, fullDate, toLocalInput } from "@/lib/meetings/format";
import { cn } from "@/lib/utils";

export function previewStatus(state: string | null | undefined, committing: boolean): ApprovalStatus {
    if (committing) return "committing";
    if (state === "armed" || state === "running" || state === "done") return "approved";
    if (state === "declined" || state === "cancelled" || state === "undone") return "cancelled";
    return "pending";
}

export function MeetingActionRow({
    meetingId,
    action,
    startOf,
    onRecord,
    onShowSource,
}: {
    meetingId: string;
    action: MeetingAction;
    /** Capture time of a transcript part, for the excerpt's label. */
    startOf: (seq: number) => number | null;
    onRecord: (record: MeetingRecord) => void;
    onShowSource: (seq: number) => void;
}) {
    const card = action.card ?? null;
    const state = card?.state ?? null;
    const [editing, setEditing] = useState(false);
    const [text, setText] = useState(action.text);
    const [owner, setOwner] = useState(action.owner_name ?? "");
    const [due, setDue] = useState(toLocalInput(action.due_at));
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);

    const call = async (run: () => Promise<{ data?: unknown; error?: unknown; response?: Response }>, fallback: string) => {
        setBusy(true);
        setProblem(null);
        const response = await run();
        setBusy(false);
        if (response.error) {
            setProblem(detailFromResult(response, fallback));
            return false;
        }
        onRecord(response.data as MeetingRecord);
        return true;
    };

    const path = { meeting_id: meetingId, item_id: action.id };
    const review = () =>
        call(() => reviewActionApiV1MeetingsMeetingIdActionsItemIdReviewPost({ path }), "Could not prepare this for approval");
    const settle = (verb: "confirm" | "decline" | "undo") =>
        call(
            () =>
                settleActionApiV1MeetingsMeetingIdActionsItemIdSettlePost({
                    path,
                    body: { verb, version: verb === "confirm" ? (card?.version ?? null) : null },
                }),
            verb === "confirm" ? "Could not approve this" : "Could not change this",
        );
    const save = async () => {
        const ok = await call(
            () =>
                editActionApiV1MeetingsMeetingIdActionsItemIdPut({
                    path,
                    body: {
                        text: text.trim(),
                        owner_name: owner.trim() || null,
                        due_at: fromLocalInput(due),
                        due_text: due ? null : action.due_text,
                    },
                }),
            "Your change was not saved. Try again.",
        );
        if (ok) setEditing(false);
    };

    const editable = state === null || ["proposed", "declined", "cancelled", "undone", "failed"].includes(state);
    const at = action.segment_seq != null ? startOf(action.segment_seq) : null;
    const when = fullDate(action.due_at);

    return (
        <li className="motion-m6-enter rounded-[8px] border border-border bg-card p-3" data-testid="meeting-action" data-state={state ?? "suggested"}>
            {!editing && (
                <>
                    <p className="break-words font-medium">{action.text}</p>
                    <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-sm">
                        <dt className="text-muted-foreground">Owner</dt>
                        <dd className="min-w-0 break-words">{action.owner_name ?? <span className="text-[#705500] dark:text-amber-300">Not named</span>}</dd>
                        <dt className="text-muted-foreground">When</dt>
                        <dd className="min-w-0 break-words">
                            {when ?? <span className="text-[#705500] dark:text-amber-300">No time said</span>}
                            {action.due_text && when && <span className="text-muted-foreground"> (“{action.due_text}”)</span>}
                            {when && state === null && <span className="text-muted-foreground"> · a suggestion until you confirm</span>}
                        </dd>
                        <dt className="text-muted-foreground">Confidence</dt>
                        <dd>{action.confidence ?? "low"}</dd>
                    </dl>
                    {action.excerpt && (
                        <button
                            type="button"
                            className="motion-m1 mt-2 flex min-h-11 w-full items-start gap-2 rounded-md bg-muted/50 px-2 py-1.5 text-left text-sm md:min-h-0"
                            onClick={() => action.segment_seq != null && onShowSource(action.segment_seq)}
                            disabled={action.segment_seq == null}
                        >
                            <Quote aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                            <span className="min-w-0 break-words">
                                “{action.excerpt}”
                                {at != null && <span className="text-muted-foreground"> · {clock(at)} in the transcript</span>}
                            </span>
                        </button>
                    )}
                    {!action.source_found && (
                        <p className="mt-1 flex items-start gap-1.5 text-sm text-[#705500] dark:text-amber-300">
                            <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                            These words were not found in the transcript. Check it before you confirm.
                        </p>
                    )}
                </>
            )}

            {editing && (
                <form
                    className="flex flex-col gap-3"
                    onSubmit={(event) => {
                        event.preventDefault();
                        void save();
                    }}
                >
                    <div className="flex flex-col gap-1">
                        <Label htmlFor={`task-${action.id}`}>Task</Label>
                        <Input id={`task-${action.id}`} className="h-11 text-base" value={text} maxLength={300} onChange={(e) => setText(e.target.value)} />
                    </div>
                    <div className="flex flex-col gap-1">
                        <Label htmlFor={`owner-${action.id}`}>Owner</Label>
                        <Input id={`owner-${action.id}`} className="h-11 text-base" value={owner} maxLength={120} onChange={(e) => setOwner(e.target.value)} />
                    </div>
                    <div className="flex flex-col gap-1">
                        <Label htmlFor={`due-${action.id}`}>When</Label>
                        <Input id={`due-${action.id}`} type="datetime-local" className="h-11 text-base" value={due} onChange={(e) => setDue(e.target.value)} />
                        {due && <p className="text-xs text-muted-foreground">{fullDate(fromLocalInput(due))}</p>}
                    </div>
                    {state === "proposed" && (
                        <p className="text-xs text-muted-foreground">Saving withdraws the approval waiting for this; you review the new version.</p>
                    )}
                    <div className="flex flex-wrap gap-2">
                        <Button type="submit" className="motion-m1 min-h-11 md:min-h-9" disabled={busy || !text.trim()}>
                            {busy ? "Saving…" : "Save"}
                        </Button>
                        <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => setEditing(false)}>
                            Cancel
                        </Button>
                    </div>
                </form>
            )}

            {!editing && state === "proposed" && card && (
                <ActionPreview
                    className="mt-3"
                    preview={{
                        id: String(card.event_id),
                        version: card.revision,
                        action: card.label ?? `Add a task: ${action.text}`,
                        recipient: card.args.owner_name ? `${card.args.owner_name} (as said in the meeting)` : undefined,
                        timing: fullDate(card.args.due_at) ?? "No due time",
                        content: card.args.task ?? action.text,
                        consequence: card.effect ?? undefined,
                    }}
                    status={previewStatus(state, busy)}
                    onApprove={() => void settle("confirm")}
                    onEdit={() => setEditing(true)}
                    onCancel={() => void settle("decline")}
                />
            )}

            {!editing && state !== "proposed" && (
                <div className="mt-3 flex flex-col gap-2">
                    {state !== null && (
                        <p
                            role="status"
                            className={cn(
                                "motion-m2 flex items-start gap-1.5 text-sm",
                                state === "done" && "text-[#075A39] dark:text-emerald-300",
                                (state === "failed" || state === "outcome_unknown") && "text-[#772322] dark:text-red-300",
                            )}
                        >
                            {state === "done" && <CheckCircle2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />}
                            {(state === "armed" || state === "running") && <Loader2 aria-hidden className="motion-continuous mt-0.5 h-4 w-4 shrink-0 animate-spin" />}
                            <span>
                                {cardLine(state)}
                                {state === "failed" && card?.error ? ` ${card.error}` : ""}
                            </span>
                        </p>
                    )}
                    <div className="flex flex-wrap gap-2">
                        {editable && (
                            <>
                                <Button type="button" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void review()}>
                                    {busy && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                                    Review
                                </Button>
                                <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => setEditing(true)}>
                                    Edit
                                </Button>
                            </>
                        )}
                        {state === "armed" && (
                            <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void settle("undo")}>
                                Undo
                            </Button>
                        )}
                        {state === "done" && card?.task_id && (
                            <>
                                <Button asChild variant="outline" className="motion-m1 min-h-11 md:min-h-9">
                                    <Link href={`/tasks/${card.task_id}`}>Open the task</Link>
                                </Button>
                                <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void settle("undo")}>
                                    Take back
                                </Button>
                            </>
                        )}
                    </div>
                </div>
            )}
            {problem && (
                <p role="alert" className="mt-2 text-sm text-[#772322] dark:text-red-300">
                    {problem}
                </p>
            )}
        </li>
    );
}

export default MeetingActionRow;
