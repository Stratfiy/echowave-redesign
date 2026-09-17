/**
 * The words a schedule is set in, and the arithmetic under them.
 *
 * Lifted out of RoutinesPanel so the Tasks board can change a time with the
 * same controls and the same clamping. Two copies of "what does 09:00 mean"
 * is how a screen that edits a routine comes to disagree with the screen
 * that made it.
 */

import type { Anchor, Cadence } from "@/client/types.gen";

/** The four cadences, in the words the person setting one would use. Not
 *  cron: the difference between two cron lines is a support ticket. */
export const CADENCES: { value: Cadence; label: string }[] = [
    { value: "hourly", label: "Every hour it is open" },
    { value: "daily", label: "Every day" },
    { value: "weekdays", label: "Monday to Friday" },
    { value: "weekly", label: "Once a week" },
];

export const ANCHORS: { value: Anchor; label: string }[] = [
    { value: "opening", label: "when it opens" },
    { value: "closing", label: "when it closes" },
    { value: "clock", label: "at a set time" },
];

export const DAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
];

export function timeToMinute(value: string): number {
    const [h, m] = value.split(":").map((n) => Number.parseInt(n, 10));
    if (!Number.isFinite(h) || !Number.isFinite(m)) return 0;
    return Math.min(24 * 60 - 1, Math.max(0, h * 60 + m));
}

/** The inverse, for filling the form from a routine being edited. Clamped
 *  the same way, so a stored minute outside the day cannot render "25:00"
 *  into a time input that would then refuse to submit. */
export function minuteToTime(minute: number): string {
    const safe = Math.min(24 * 60 - 1, Math.max(0, Math.trunc(minute) || 0));
    const h = String(Math.floor(safe / 60)).padStart(2, "0");
    const m = String(safe % 60).padStart(2, "0");
    return `${h}:${m}`;
}

/** A date and time somebody can read, or "" for nothing scheduled. */
export function when(iso: string | null | undefined): string {
    if (!iso) return "";
    return new Date(iso).toLocaleString(undefined, {
        weekday: "short",
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
    });
}
