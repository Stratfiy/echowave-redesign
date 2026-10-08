"use client";

/**
 * One upcoming event with the reminders linked to it. Moving it shows each
 * reminder's old and new time, recalculated by the offset the person chose;
 * a reminder the move puts in the past is named, not silently dropped.
 * Cancelling the event cancels its reminders.
 */

import { useState } from "react";

import { cancelEventApiV1TodayEventsEventIdCancelPost, moveEventApiV1TodayEventsEventIdMovePost } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { detailFromError } from "@/lib/apiError";
import type { UpcomingEvent } from "@/lib/today/types";

const OFFSET: Record<string, string> = { "0": "At event time", "-1440": "One day before" };

type Change = { reminder_id: number; offset_words: string; old: string | null; new: string; conflict: string | null };

export function EventRow({ event, onChanged }: { event: UpcomingEvent; onChanged: (line: string) => void }) {
    const [moving, setMoving] = useState(false);
    const [day, setDay] = useState(event.at.slice(0, 10));
    const [time, setTime] = useState("09:00");
    const [changes, setChanges] = useState<Change[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    async function move() {
        setBusy(true);
        setError(null);
        const result = await moveEventApiV1TodayEventsEventIdMovePost({
            path: { event_id: event.id },
            body: { date: day, local_time: time, revision: event.revision },
        });
        setBusy(false);
        if (result.error) {
            setError(detailFromError(result.error, "The event was not moved."));
            return;
        }
        const data = result.data as { changes: Change[]; when: string };
        setChanges(data.changes);
        setMoving(false);
        onChanged(`Moved ${event.title} to ${data.when}.`);
    }

    async function cancel() {
        setBusy(true);
        const result = await cancelEventApiV1TodayEventsEventIdCancelPost({ path: { event_id: event.id } });
        setBusy(false);
        if (result.error) {
            setError(detailFromError(result.error, "The event was not cancelled."));
            return;
        }
        const n = (result.data as { cancelled_reminders: number[] }).cancelled_reminders.length;
        onChanged(`Cancelled ${event.title} and ${n} reminder${n === 1 ? "" : "s"} linked to it.`);
    }

    return (
        <li className="flex flex-col gap-1 rounded-md px-3 py-2" data-testid={`event-${event.id}`}>
            <p className="break-words text-sm font-medium">{event.title}</p>
            <p className="text-xs text-muted-foreground">{event.when}</p>
            {event.reminders.length > 0 && (
                <p className="text-xs text-muted-foreground">
                    Reminders: {event.reminders.map((r) => OFFSET[String(r.offset_minutes)] ?? `${r.offset_minutes} minutes`).join(", ")}
                </p>
            )}
            {changes && changes.length > 0 && (
                <ul className="motion-m2-enter text-xs" aria-label="What changed">
                    {changes.map((c) => (
                        <li key={c.reminder_id} className="break-words">
                            {c.offset_words}: {c.old ?? "—"} → {c.new}
                            {c.conflict && <span className="block text-[#705500] dark:text-amber-300">{c.conflict}</span>}
                        </li>
                    ))}
                </ul>
            )}
            {moving ? (
                <form
                    className="flex flex-col gap-2 sm:flex-row sm:items-end"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void move();
                    }}
                >
                    <label className="flex flex-col gap-1 text-xs">
                        New date
                        <Input type="date" value={day} onChange={(e) => setDay(e.target.value)} className="min-h-11 text-base md:min-h-9 md:text-sm" required />
                    </label>
                    <label className="flex flex-col gap-1 text-xs">
                        New time ({event.timezone})
                        <Input type="time" value={time} onChange={(e) => setTime(e.target.value)} className="min-h-11 text-base md:min-h-9 md:text-sm" required />
                    </label>
                    <div className="flex gap-2">
                        <Button type="submit" className="motion-m1 min-h-11 md:min-h-9" disabled={busy}>
                            Move it
                        </Button>
                        <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" onClick={() => setMoving(false)}>
                            Keep it
                        </Button>
                    </div>
                </form>
            ) : (
                <div className="flex flex-wrap gap-2">
                    <Button variant="ghost" className="motion-m1 min-h-11 md:min-h-9" onClick={() => setMoving(true)}>
                        Move
                    </Button>
                    <Button variant="ghost" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void cancel()}>
                        Cancel event
                    </Button>
                </div>
            )}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
        </li>
    );
}

export default EventRow;
