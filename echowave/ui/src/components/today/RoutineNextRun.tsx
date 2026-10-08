"use client";

/**
 * The live next-run sentence for a routine being edited (screen 10), shown
 * directly above Save: "Every Monday at 10:00. Next run: Mon 12 Oct 2026,
 * 10:00 IST (Asia/Kolkata)." Read from the server, in the workspace's zone
 * and hours, so the sentence is the one the clock will follow.
 */

import { useEffect, useState } from "react";

import { routinePreviewApiV1TodayRoutinesPreviewPost } from "@/client/sdk.gen";
import type { ScheduleValue } from "@/components/workflow/ScheduleFields";
import { timeToMinute } from "@/lib/schedule";

export function RoutineNextRun({ value, offsetMinutes = 0 }: { value: ScheduleValue; offsetMinutes?: number }) {
    const [sentence, setSentence] = useState<string | null>(null);

    useEffect(() => {
        const handle = setTimeout(async () => {
            const result = await routinePreviewApiV1TodayRoutinesPreviewPost({
                body: {
                    cadence: value.cadence,
                    anchor: value.anchor,
                    at_minute: timeToMinute(value.time),
                    offset_minutes: offsetMinutes,
                    weekday: value.weekday,
                },
            });
            setSentence(result.error ? "That schedule cannot be read." : (result.data as { sentence: string }).sentence);
        }, 300);
        return () => clearTimeout(handle);
    }, [value, offsetMinutes]);

    return (
        <div className="motion-m2 flex flex-col gap-1 text-sm" aria-live="polite" data-testid="routine-next-run">
            <p>{sentence ?? "Working out the next run…"}</p>
            <p className="text-xs text-muted-foreground">Switching it off stops future runs; a run already started finishes.</p>
        </div>
    );
}

export default RoutineNextRun;
