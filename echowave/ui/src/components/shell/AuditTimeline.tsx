"use client";

/**
 * Who did what, to what, why and with what result, newest first. The
 * presentational half of the staff console's audit (AuditLog fetches; this
 * draws one record's history anywhere: a task, a support case, a flag).
 */

import { EmptyState } from "@/components/EmptyState";
import { actionLabel } from "@/components/superadmin/AuditLog";
import { cn } from "@/lib/utils";

export type AuditTimelineEntry = {
    id: string | number;
    at: string;
    actor: string;
    /** A machine action name ("flag_changed") or a sentence. */
    action: string;
    target?: string;
    reason?: string;
    /** "Succeeded", "Queued", "Failed: provider refused". */
    result?: string;
};

function when(at: string): string {
    const date = new Date(at);
    if (Number.isNaN(date.getTime())) return at;
    return date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export function AuditTimeline({
    entries,
    emptyTitle = "Nothing has been done here yet.",
    className,
}: {
    entries: readonly AuditTimelineEntry[];
    emptyTitle?: string;
    className?: string;
}) {
    if (entries.length === 0) return <EmptyState title={emptyTitle} />;
    const ordered = [...entries].sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
    return (
        <ol className={cn("relative flex flex-col gap-4 border-l border-border pl-4", className)} aria-label="Activity history">
            {ordered.map((entry) => (
                <li key={entry.id} className="relative" data-testid="audit-entry">
                    <span aria-hidden className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-background bg-muted-foreground" />
                    <p className="text-sm">
                        <span className="font-medium">{entry.actor}</span>{" "}
                        <span>{/^[a-z_]+$/.test(entry.action) ? actionLabel(entry.action).toLowerCase() : entry.action}</span>
                        {entry.target && <span className="text-muted-foreground"> · {entry.target}</span>}
                    </p>
                    <p className="text-xs text-muted-foreground">
                        <time dateTime={entry.at}>{when(entry.at)}</time>
                        {entry.result && <> · {entry.result}</>}
                    </p>
                    {entry.reason && <p className="mt-0.5 break-words text-xs">Reason: {entry.reason}</p>}
                </li>
            ))}
        </ol>
    );
}

export default AuditTimeline;
