"use client";

/**
 * Support actions waiting on a decision, and the recent ones -- so the
 * second person can find what needs their approval.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState } from "@/components/shell";
import { ACTION_STATE_LABEL, listActions, type SupportAction } from "@/lib/support/staff";
import { cn } from "@/lib/utils";

export function ActionsList() {
    const [view, setView] = useState<"pending" | "">("pending");
    const [rows, setRows] = useState<SupportAction[] | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const outcome = await listActions({ state: view || undefined });
        if (outcome.ok) {
            setRows(outcome.value);
            setError(null);
        } else setError(outcome.error);
    }, [view]);

    useEffect(() => {
        setRows(null);
        void load();
    }, [load]);

    return (
        <div className="flex flex-col gap-3">
            <div role="tablist" aria-label="Which actions" className="flex gap-1">
                {(
                    [
                        ["pending", "Waiting on a decision"],
                        ["", "All recent"],
                    ] as const
                ).map(([value, label]) => (
                    <button
                        key={label}
                        type="button"
                        role="tab"
                        aria-selected={view === value}
                        onClick={() => setView(value)}
                        className={cn("motion-m1 min-h-11 rounded-md px-3 text-sm md:min-h-9", view === value ? "bg-muted font-medium" : "text-muted-foreground hover:bg-muted/50")}
                    >
                        {label}
                    </button>
                ))}
            </div>
            {error && !rows ? (
                <ErrorState title="Actions could not load." description={error} onRetry={() => void load()} />
            ) : !rows ? (
                <p role="status" className="text-sm text-muted-foreground">
                    Loading actions…
                </p>
            ) : rows.length === 0 ? (
                <EmptyState title={view === "pending" ? "Nothing is waiting on a decision." : "No support actions yet."} />
            ) : (
                <ul className="divide-y divide-border rounded-[var(--radius)] border border-border">
                    {rows.map((row) => (
                        <li key={row.id}>
                            <Link href={`/superadmin/support/actions/${row.id}`} className="motion-m1 flex min-h-11 flex-col gap-0.5 px-3 py-2.5 hover:bg-muted/40 sm:flex-row sm:items-center sm:gap-3" data-testid="action-row">
                                <span className="min-w-0 flex-1 break-words text-sm font-medium">{row.preview.title}</span>
                                <span className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                                    <span>{ACTION_STATE_LABEL[row.state] ?? row.state}</span>
                                    <span>· by {row.requested_by}</span>
                                    {row.ticket_id && <span>· ticket #{row.ticket_id}</span>}
                                </span>
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

export default ActionsList;
