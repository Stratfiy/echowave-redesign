/**
 * Cutting the handed-over rows into days.
 *
 * This lives beside the page rather than in it because a Next page file may
 * only export the page itself — and the grouping is the one piece of logic
 * on that screen worth holding still in a test.
 */

import type { TimelineEvent } from "@/client/types.gen";

/** Today and yesterday by name: that is how somebody scrolling back says it. */
export function dayLabel(at: string): string {
    const date = new Date(at);
    if (Number.isNaN(date.getTime())) return "";
    const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((midnight(new Date()) - midnight(date)) / 86_400_000);
    if (days === 0) return "Today";
    if (days === 1) return "Yesterday";
    return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** The rows in order, cut into days. */
export function byDay(events: TimelineEvent[]): { day: string; events: TimelineEvent[] }[] {
    const days: { day: string; events: TimelineEvent[] }[] = [];
    for (const event of events) {
        const label = dayLabel(event.at);
        const last = days[days.length - 1];
        if (last && last.day === label) last.events.push(event);
        else days.push({ day: label, events: [event] });
    }
    return days;
}
