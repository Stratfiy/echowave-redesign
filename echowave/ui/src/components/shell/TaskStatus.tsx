"use client";

/**
 * A task's state, the same way on every screen (handoff "Task state").
 *
 * Icon and words, never colour alone. A running task names its real stage
 * ("Read the team and 3 passages") when there is one; there is no percent,
 * because there is nothing measured to put in it. Evidence (a message id, a
 * calendar entry) sits beside a finished state so "Done" can be checked.
 */

import {
    Ban,
    CalendarClock,
    CheckCircle2,
    CircleDashed,
    CirclePause,
    HelpCircle,
    Loader2,
    MessageCircleQuestion,
    ShieldQuestion,
    XCircle,
} from "lucide-react";
import type { ReactNode } from "react";

import { TASK_STATE_LABEL, type TaskState } from "@/lib/shell/taskState";
import { cn } from "@/lib/utils";

const ICON: Record<TaskState, typeof CheckCircle2> = {
    queued: CircleDashed,
    running: Loader2,
    needs_input: MessageCircleQuestion,
    awaiting_approval: ShieldQuestion,
    scheduled: CalendarClock,
    completed: CheckCircle2,
    failed: XCircle,
    cancelled: Ban,
    outcome_unknown: HelpCircle,
    partial: CirclePause,
};

/** Tones from the handoff's status palette, each with its text label. */
const TONE: Record<TaskState, string> = {
    queued: "text-muted-foreground",
    running: "text-muted-foreground",
    needs_input: "text-[#705500] dark:text-amber-300",
    awaiting_approval: "text-[#705500] dark:text-amber-300",
    scheduled: "text-muted-foreground",
    completed: "text-[#075A39] dark:text-emerald-300",
    failed: "text-[#772322] dark:text-red-300",
    cancelled: "text-muted-foreground",
    outcome_unknown: "text-[#705500] dark:text-amber-300",
    partial: "text-[#705500] dark:text-amber-300",
};

export function TaskStatus({
    state,
    stage,
    evidence,
    label,
    className,
}: {
    state: TaskState;
    /** The real step in progress, for a running task. */
    stage?: string;
    /** What proves a finished state: a message id, a calendar entry. */
    evidence?: ReactNode;
    /** Override the standard words, e.g. "Sent" for a completed send. */
    label?: string;
    className?: string;
}) {
    const Icon = ICON[state];
    const words = label ?? TASK_STATE_LABEL[state];
    return (
        <span
            className={cn("motion-m2 inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs", TONE[state], className)}
            data-state={state}
            data-testid="task-status"
        >
            <Icon
                aria-hidden
                className={cn("h-3.5 w-3.5 shrink-0", state === "running" && "motion-continuous animate-spin")}
            />
            <span className="font-medium">{words}</span>
            {stage && state !== "completed" && <span className="text-muted-foreground">· {stage}</span>}
            {evidence && <span className="text-muted-foreground">· {evidence}</span>}
        </span>
    );
}

export default TaskStatus;
