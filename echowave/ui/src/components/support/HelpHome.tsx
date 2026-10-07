"use client";

/**
 * Help: the person's own requests in this workspace, newest activity first,
 * and the way to ask for help. "Support replied" marks a request whose last
 * word is support's, so nothing waits unseen.
 */

import { LifeBuoy, Plus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { listTickets, STATUS_LABEL, type TicketSummary } from "@/lib/support/help";

function when(at?: string | null): string {
    if (!at) return "";
    const date = new Date(at);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function HelpHome() {
    const [tickets, setTickets] = useState<TicketSummary[] | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const outcome = await listTickets();
        if (outcome.ok) {
            setTickets(outcome.value);
            setError(null);
        } else setError(outcome.error);
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    return (
        <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-3">
                <h1 className="flex-1 text-2xl font-semibold">Help</h1>
                <Button asChild className="motion-m1 min-h-11 md:min-h-9">
                    <Link href="/help/new">
                        <Plus aria-hidden className="h-4 w-4" /> Ask support
                    </Link>
                </Button>
            </div>
            <p className="text-sm text-muted-foreground">
                Tell us what went wrong. Before anything is sent you will see exactly what support can see.
            </p>
            {error && !tickets ? (
                <ErrorState title="Your requests could not load." description={error} onRetry={() => void load()} />
            ) : !tickets ? (
                <p role="status" className="text-sm text-muted-foreground">
                    Loading your requests…
                </p>
            ) : tickets.length === 0 ? (
                <EmptyState icon={LifeBuoy} title="No requests yet." description="When something does not work, ask here and follow the answer." />
            ) : (
                <ul className="divide-y divide-border rounded-[var(--radius)] border border-border" aria-label="Your requests">
                    {tickets.map((ticket) => (
                        <li key={ticket.id}>
                            <Link
                                href={`/help/${ticket.id}`}
                                className="motion-m1 flex min-h-11 flex-col gap-0.5 px-4 py-3 hover:bg-muted/40 sm:flex-row sm:items-center sm:gap-3"
                                data-testid="help-ticket-row"
                            >
                                <span className="min-w-0 flex-1 break-words text-sm font-medium">{ticket.subject}</span>
                                <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                    {ticket.support_replied && ticket.status !== "resolved" && (
                                        <span className="rounded-full bg-primary px-2 py-0.5 font-medium text-primary-foreground">Support replied</span>
                                    )}
                                    <span>{STATUS_LABEL[ticket.status] ?? ticket.status}</span>
                                    <span>· {when(ticket.updated_at)}</span>
                                </span>
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

export default HelpHome;
