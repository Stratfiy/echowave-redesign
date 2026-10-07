"use client";

/**
 * Screen 32, the queue: assignee, severity, age and next step first, with
 * filters for status, assignee and severity. Oldest first and stable, so a
 * case being worked does not move under the cursor when another changes.
 */

import { AlertTriangle, RefreshCw } from "lucide-react";
import Link from "next/link";

import { EmptyState, ErrorState } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { CASE_STATUS_LABEL, type QueueRow, SEVERITIES } from "@/lib/support/staff";
import { cn } from "@/lib/utils";

export type QueueFilters = { status: string; assignee: string; severity: string; overdue: boolean };

function age(at: string | null): string {
    if (!at) return "";
    const minutes = Math.max(0, Math.round((Date.now() - new Date(at).getTime()) / 60000));
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.round(minutes / 60);
    return hours < 48 ? `${hours}h` : `${Math.round(hours / 24)}d`;
}

const SELECT = "min-h-11 rounded-[var(--radius-control)] border border-input bg-background px-2 text-base md:min-h-9 md:text-sm";

export function SupportQueue({
    rows,
    error,
    loadedAt,
    filters,
    onFilters,
    onRefresh,
    selectedId,
    staffNames,
    className,
}: {
    rows: QueueRow[] | null;
    error: string | null;
    loadedAt: Date | null;
    filters: QueueFilters;
    onFilters: (next: QueueFilters) => void;
    onRefresh: () => void;
    selectedId: number | null;
    staffNames: Record<number, string>;
    className?: string;
}) {
    return (
        <section aria-label="Support queue" className={cn("flex min-h-0 flex-col", className)}>
            <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
                <label className="sr-only" htmlFor="queue-status">
                    Status
                </label>
                <select id="queue-status" className={SELECT} value={filters.status} onChange={(e) => onFilters({ ...filters, status: e.target.value })}>
                    <option value="active">Not resolved</option>
                    {Object.entries(CASE_STATUS_LABEL).map(([value, label]) => (
                        <option key={value} value={value}>
                            {label}
                        </option>
                    ))}
                </select>
                <label className="sr-only" htmlFor="queue-assignee">
                    Assignee
                </label>
                <select id="queue-assignee" className={SELECT} value={filters.assignee} onChange={(e) => onFilters({ ...filters, assignee: e.target.value })}>
                    <option value="">Anyone</option>
                    <option value="me">Mine</option>
                    <option value="unassigned">Unassigned</option>
                </select>
                <label className="sr-only" htmlFor="queue-severity">
                    Severity
                </label>
                <select id="queue-severity" className={SELECT} value={filters.severity} onChange={(e) => onFilters({ ...filters, severity: e.target.value })}>
                    <option value="">Any severity</option>
                    {SEVERITIES.map((s) => (
                        <option key={s} value={s}>
                            {s}
                        </option>
                    ))}
                </select>
                <label className="flex min-h-11 items-center gap-1.5 text-sm md:min-h-9">
                    <input type="checkbox" className="h-4 w-4" checked={filters.overdue} onChange={(e) => onFilters({ ...filters, overdue: e.target.checked })} />
                    Overdue
                </label>
                <Button type="button" variant="ghost" size="icon" className="motion-m1 ml-auto h-11 w-11 md:h-9 md:w-9" aria-label="Refresh" onClick={onRefresh}>
                    <RefreshCw aria-hidden className="h-4 w-4" />
                </Button>
            </div>
            {loadedAt && (
                <p className="px-3 pt-2 text-xs text-muted-foreground" data-testid="queue-freshness">
                    Updated {loadedAt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}
                    {error && rows ? " · refresh failed, showing the last list" : ""}
                </p>
            )}
            <div className="min-h-0 flex-1 overflow-y-auto">
                {error && !rows ? (
                    <ErrorState title="The queue could not load." description={error} onRetry={onRefresh} />
                ) : !rows ? (
                    <p role="status" className="p-4 text-sm text-muted-foreground">
                        Loading the queue…
                    </p>
                ) : rows.length === 0 ? (
                    <EmptyState title="No cases match." description="Change the filters to see more." />
                ) : (
                    <ul className="divide-y divide-border">
                        {rows.map((row) => (
                            <li key={row.id}>
                                <Link
                                    href={`/superadmin/support/${row.id}`}
                                    aria-current={selectedId === row.id ? "page" : undefined}
                                    className={cn(
                                        "motion-m1 flex min-h-11 flex-col gap-1 px-3 py-2.5 hover:bg-muted/40",
                                        selectedId === row.id && "bg-muted",
                                    )}
                                    data-testid="queue-row"
                                >
                                    <span className="flex items-start gap-2">
                                        <span className="min-w-0 flex-1 break-words text-sm font-medium">{row.subject}</span>
                                        <span className="shrink-0 text-xs text-muted-foreground">{age(row.created_at)}</span>
                                    </span>
                                    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                                        <span className={cn(row.severity === "urgent" || row.severity === "high" ? "font-semibold text-[#772322] dark:text-red-300" : "")}>
                                            {row.severity}
                                        </span>
                                        <span>· {row.assignee_user_id ? staffNames[row.assignee_user_id] ?? `Staff #${row.assignee_user_id}` : "Unassigned"}</span>
                                        <span>· {CASE_STATUS_LABEL[row.status] ?? row.status}</span>
                                        <span>· Next: {row.next_step}</span>
                                        {row.overdue && (
                                            <span className="flex items-center gap-0.5 font-semibold text-[#705500] dark:text-amber-300">
                                                <AlertTriangle aria-hidden className="h-3 w-3" /> Overdue
                                            </span>
                                        )}
                                        {row.linked_incident && <span>· {row.linked_incident}</span>}
                                    </span>
                                    <span className="truncate text-xs text-muted-foreground">
                                        {row.requester_email} · {row.workspace_name}
                                    </span>
                                </Link>
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </section>
    );
}

export default SupportQueue;
