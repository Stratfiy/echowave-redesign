"use client";

/**
 * What this bot achieved, on the bot's own screen.
 *
 * The outcome taxonomy has existed for months and the classifier has filled
 * it in on every completed run. Nothing read it back. An operator could tick
 * "booked" as an outcome, watch a hundred calls get classified, and have no
 * way to ask the only question they actually have -- *how many did it book*
 * -- short of paging the calls list and counting chips by eye.
 *
 * Here rather than on a dashboard, and above the run list rather than beside
 * it. A dashboard averages every bot in the account into one number, which
 * mixes the clinic's bookings with the collections agent's payment promises
 * and means nothing. This is the bot you are already looking at, and the
 * reading order is the order the questions come: what did it achieve, then
 * which calls were those.
 *
 * Zeros are shown. A board drawn only where an outcome fired reads as "this
 * bot does three things"; the zero is usually the finding -- nobody has ever
 * been booked -- and it is the one row somebody needs to see.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { getOutcomeBoardApiV1WorkflowOutcomesGet } from "@/client/sdk.gen";
import type { BotOutcomesResponse } from "@/client/types.gen";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { detailFromResult } from "@/lib/apiError";
import { cn } from "@/lib/utils";

/** The same three the Analytics range picker offers. Two pickers with
 *  different windows is how a reader compares numbers over different days. */
const WINDOWS = [7, 30, 90] as const;

function percent(count: number, of: number): string {
    if (of <= 0) return "—";
    return `${Math.round((count / of) * 100)}%`;
}

export function BotResults({ workflowId }: { workflowId: number }) {
    const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
    const [board, setBoard] = useState<BotOutcomesResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        (async () => {
            const response = await getOutcomeBoardApiV1WorkflowOutcomesGet({
                query: { days, workflow_id: workflowId },
            });
            if (cancelled) return;
            setLoading(false);
            // The generated client resolves on a 4xx rather than throwing, so
            // the error has to be read off the result or this renders an
            // empty board for a request that failed.
            if (response.error) {
                setError(detailFromResult(response, "Results could not be loaded"));
                return;
            }
            setError(null);
            setBoard(response.data?.[0] ?? null);
        })();
        return () => {
            cancelled = true;
        };
    }, [workflowId, days]);

    const settings = `/workflow/${workflowId}/settings?tab=analysis`;

    return (
        <Card data-testid="bot-results">
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 space-y-0">
                <CardTitle className="text-base">Results</CardTitle>
                <div className="flex items-center gap-1 rounded-lg border p-0.5">
                    {WINDOWS.map((window) => (
                        <button
                            key={window}
                            type="button"
                            onClick={() => setDays(window)}
                            className={cn(
                                "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                                days === window
                                    ? "bg-accent text-foreground"
                                    : "text-muted-foreground hover:text-foreground",
                            )}
                        >
                            {window}d
                        </button>
                    ))}
                </div>
            </CardHeader>
            <CardContent>
                {error ? (
                    <p className="text-sm text-destructive">{error}</p>
                ) : loading ? (
                    <p className="text-sm text-muted-foreground">Counting…</p>
                ) : !board ? (
                    <p className="text-sm text-muted-foreground">
                        This agent could not be found.
                    </p>
                ) : board.runs === 0 ? (
                    <p className="text-sm text-muted-foreground">
                        No runs in the last {days} days.
                    </p>
                ) : (
                    <>
                        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                            {board.outcomes.map((outcome) => (
                                <div key={outcome.code}>
                                    <p className="text-sm text-muted-foreground">
                                        {outcome.label}
                                    </p>
                                    <p className="text-2xl font-semibold tabular-nums">
                                        {outcome.count}
                                        <span className="ml-2 text-sm font-normal text-muted-foreground">
                                            {percent(outcome.count, board.classified)}
                                        </span>
                                    </p>
                                </div>
                            ))}
                        </div>

                        {/* The denominator, said out loud. A rate over runs the
                            classifier never reached is a rate over a number
                            nobody can see, and a bot whose classification has
                            been failing would otherwise look like a bot that
                            achieved nothing. */}
                        <p className="mt-4 text-xs text-muted-foreground">
                            {board.classified} of {board.runs} run
                            {board.runs === 1 ? "" : "s"} sorted
                            {board.truncated ? ", counting the most recent runs only" : ""}.{" "}
                            <Link href={settings} className="underline underline-offset-4">
                                {board.configured ? "Change what counts" : "Say what counts"}
                            </Link>
                        </p>

                        {/* Whose outcomes these are, when they are not this
                            bot's. A zero under a heading the business chose is
                            a finding; the same zero under a list they have
                            never seen is an invitation to choose one, and
                            presenting the two identically is how a default
                            gets mistaken for a decision. */}
                        {!board.configured && (
                            <p className="mt-1 text-xs text-muted-foreground">
                                These are the default outcomes — nobody has said what a
                                win is for this agent yet.
                            </p>
                        )}
                    </>
                )}
            </CardContent>
        </Card>
    );
}
