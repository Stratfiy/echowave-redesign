"use client";

/**
 * Reminder calls in Today (behind `reminder_calls`): every call Decibyl
 * will ring the person with, next ring first, with Cancel right here. A
 * call that rang and is still open can be marked done here too. The task
 * (did you deal with it) and each ring (did the call happen) are shown
 * side by side and never folded into one: services/reminder_calls.
 *
 * Draws nothing while the flag is off.
 */

import { Phone } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    cancelReminderCallApiV1ReminderCallsScheduleIdCancelPost,
    listReminderCallsApiV1ReminderCallsGet,
    markReminderDoneApiV1ReminderCallsOccurrencesOccurrenceIdDonePost,
} from "@/client/sdk.gen";
import type { ReminderCall } from "@/client/types.gen";
import { ErrorState } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

/** What one ring attempt came to, in the person's words. */
const RING_WORDS: Record<string, string> = {
    queued: "waiting to ring",
    dispatching: "ringing now",
    accepted: "ringing now",
    answered: "answered",
    no_answer: "not answered",
    failed: "could not connect",
    unknown: "can't confirm it reached you",
    skipped: "not rung",
};

const REPEAT_WORDS: Record<string, string> = {
    once: "Once",
    daily: "Every day",
    weekdays: "Every weekday",
    weekly: "Every week",
};

function lastRing(reminder: ReminderCall): string | null {
    const latest = reminder.recent[0];
    const ring = latest?.calls[latest.calls.length - 1];
    if (!ring) return null;
    return `Last call: ${RING_WORDS[ring.state] ?? ring.state}.`;
}

/** The latest occurrence, when it rang and the task is still open. */
function openAfterARing(reminder: ReminderCall) {
    const latest = reminder.recent[0];
    if (!latest || latest.task_state !== "open" || latest.calls.length === 0) return null;
    return latest;
}

export function ReminderCallsSection({ onAnnounce }: { onAnnounce?: (line: string) => void }) {
    const on = useFeature("reminder_calls");
    const { user, loading: authLoading } = useAuth();
    const signedIn = Boolean(user);
    const [reminders, setReminders] = useState<ReminderCall[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [rowError, setRowError] = useState<string | null>(null);
    const [busy, setBusy] = useState<number | null>(null);

    const load = useCallback(async () => {
        const result = await listReminderCallsApiV1ReminderCallsGet();
        if (result.error) {
            setError(detailFromError(result.error, "Reminder calls could not load."));
            return;
        }
        setError(null);
        setReminders(result.data?.reminders ?? []);
    }, []);

    useEffect(() => {
        if (!on || authLoading || !signedIn) return;
        void load();
    }, [on, authLoading, signedIn, load]);

    if (!on) return null;

    async function cancel(reminder: ReminderCall) {
        setBusy(reminder.id);
        setRowError(null);
        const result = await cancelReminderCallApiV1ReminderCallsScheduleIdCancelPost({ path: { schedule_id: reminder.id } });
        setBusy(null);
        if (result.error) {
            setRowError(detailFromError(result.error, "That did not go through."));
            return;
        }
        onAnnounce?.(`Cancelled: ${reminder.title}. Decibyl won't call you about it.`);
        await load();
    }

    async function done(reminder: ReminderCall, occurrenceId: number) {
        setBusy(reminder.id);
        setRowError(null);
        const result = await markReminderDoneApiV1ReminderCallsOccurrencesOccurrenceIdDonePost({ path: { occurrence_id: occurrenceId } });
        setBusy(null);
        if (result.error) {
            setRowError(detailFromError(result.error, "That did not go through."));
            return;
        }
        onAnnounce?.(`Done: ${reminder.title}.`);
        await load();
    }

    const shown = (reminders ?? []).filter((r) => (r.state === "active" && r.next_due_at) || openAfterARing(r));
    return (
        <section aria-labelledby="today-reminder-calls" className="flex flex-col gap-2" data-testid="today-section-reminder-calls">
            <h2 id="today-reminder-calls" className="flex items-center gap-2 text-base font-semibold">
                Reminder calls
            </h2>
            {error ? (
                <ErrorState title={error} onRetry={() => void load()} />
            ) : reminders === null ? (
                <p className="px-3 text-sm text-muted-foreground" aria-busy="true">
                    Loading reminder calls…
                </p>
            ) : shown.length === 0 ? (
                <p className="px-3 text-sm text-muted-foreground">No reminder calls coming up.</p>
            ) : (
                <ul className="flex flex-col gap-1">
                    {shown.map((reminder) => {
                        const open = openAfterARing(reminder);
                        const ring = lastRing(reminder);
                        return (
                            <li key={reminder.id} className="flex flex-col gap-1 rounded-md px-3 py-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between" data-testid="reminder-call-row">
                                <div className="min-w-0">
                                    <p className="flex items-start gap-2 break-words text-sm font-medium">
                                        <Phone aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                                        <span>{reminder.title}</span>
                                    </p>
                                    {reminder.state === "active" && reminder.next_due_label && (
                                        <p className="text-xs text-muted-foreground">
                                            Next call: {reminder.next_due_label} · {reminder.number} · {REPEAT_WORDS[reminder.recurrence] ?? reminder.recurrence}
                                        </p>
                                    )}
                                    {ring && <p className="text-xs text-muted-foreground">{ring}</p>}
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    {open && (
                                        <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy === reminder.id} onClick={() => void done(reminder, open.id)}>
                                            Done
                                        </Button>
                                    )}
                                    {reminder.state === "active" && (
                                        <Button variant="ghost" className="motion-m1 min-h-11 md:min-h-9" disabled={busy === reminder.id} onClick={() => void cancel(reminder)}>
                                            Cancel call
                                        </Button>
                                    )}
                                </div>
                            </li>
                        );
                    })}
                </ul>
            )}
            {rowError && (
                <p role="alert" className="px-3 text-sm text-destructive">
                    {rowError}
                </p>
            )}
        </section>
    );
}

export default ReminderCallsSection;
