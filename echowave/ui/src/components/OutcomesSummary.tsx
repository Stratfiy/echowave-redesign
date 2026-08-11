"use client";

import { useEffect, useRef, useState } from "react";

import { getUsageOutcomesApiV1OrganizationsUsageOutcomesGet } from "@/client/sdk.gen";
import type { OutcomesResponse } from "@/client/types.gen";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { useAuth } from "@/lib/auth";
import { formatPaise } from "@/lib/billing/format";

/**
 * Answer rate and cost per outcome — the numbers that say whether the
 * product is working and whether it pays. There is no single universal
 * "booking" metric across accounts (every workflow's End Call tool or
 * prompt produces its own disposition strings), so this shows cost per
 * call for every outcome this account's calls actually produced; read
 * whichever row is your own success outcome.
 */
export function OutcomesSummary({ days = 7 }: { days?: number }) {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    const [outcomes, setOutcomes] = useState<OutcomesResponse | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void fetchOutcomes();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [authLoading, user]);

    async function fetchOutcomes() {
        setLoading(true);
        try {
            const response = await getUsageOutcomesApiV1OrganizationsUsageOutcomesGet({
                query: { days },
            });
            if (!response.error && response.data) {
                setOutcomes(response.data);
            }
        } finally {
            setLoading(false);
        }
    }

    if (loading) {
        return <p className="text-sm text-muted-foreground">Loading...</p>;
    }
    if (!outcomes) {
        return null;
    }

    const { answer_seizure_ratio: asr, cost_by_outcome: byOutcome } = outcomes;

    return (
        <div className="mb-6 space-y-4">
            <div className="rounded-xl border bg-card p-4">
                <p className="text-sm text-muted-foreground">
                    Answer rate (last {days} days)
                </p>
                <p className="text-2xl font-semibold tabular-nums">
                    {asr.asr == null ? "—" : `${(asr.asr * 100).toFixed(1)}%`}
                </p>
                <p className="text-xs text-muted-foreground">
                    {asr.connected} answered of {asr.attempted} attempted
                </p>
            </div>

            {byOutcome.length > 0 && (
                <div className="rounded-xl border bg-card p-4">
                    <p className="mb-2 text-sm text-muted-foreground">
                        Cost by outcome (last {days} days)
                    </p>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Outcome</TableHead>
                                <TableHead className="text-right">Calls</TableHead>
                                <TableHead className="text-right">Total cost</TableHead>
                                <TableHead className="text-right">
                                    Cost per call
                                </TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {byOutcome.map((row) => (
                                <TableRow key={row.disposition}>
                                    <TableCell className="whitespace-nowrap">
                                        {row.disposition}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {row.calls}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatPaise(row.total_charged_paise)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatPaise(row.cost_per_call_paise)}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}
        </div>
    );
}
