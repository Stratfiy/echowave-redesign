/**
 * Tasks: everything that runs on its own, across every bot.
 *
 * Routines were reachable one bot at a time, which answers "what does this
 * bot do" and never "what runs tomorrow morning" -- the question somebody
 * actually has, and one they could not ask without already knowing which
 * bots to open. So the schedule the business runs on was invisible.
 *
 * Read-only here on purpose. A routine is created on the bot that runs it,
 * because the instruction only means anything next to the bot's own prompt
 * and tools; this screen is where you see them all and go to the one you
 * want.
 */

"use client";

import { CalendarClock } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { listAllRoutinesApiV1RoutinesGet } from "@/client/sdk.gen";
import { HOME_TABS } from "@/components/home/tabs";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import SpinLoader from "@/components/SpinLoader";
import { Card, CardContent } from "@/components/ui/card";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type Routine = {
    id: number;
    workflow_id: number;
    workflow_name?: string | null;
    name: string;
    schedule_summary?: string | null;
    next_run_at?: string | null;
    is_active: boolean;
};

export default function TasksPage() {
    const { user, loading: authLoading } = useAuth();
    const [routines, setRoutines] = useState<Routine[] | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (authLoading || !user) return;
        const result = await listAllRoutinesApiV1RoutinesGet();
        if (result.error) {
            setError(detailFromResult(result, "Could not load the schedule"));
            setRoutines([]);
            return;
        }
        setRoutines((result.data?.routines ?? []) as Routine[]);
    }, [authLoading, user]);

    useEffect(() => {
        void load();
    }, [load]);

    return (
        <>
            <PageHeader
                title="Decibyl"
                description="What runs on its own, and when."
                tabs={HOME_TABS}
            />
            <PageBody className="space-y-6">
                {error && (
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {error}
                    </div>
                )}

                {routines === null ? (
                    <SpinLoader />
                ) : routines.length === 0 ? (
                    // Says where one is made, because this screen cannot make
                    // one and an empty list that only says "none" is a dead end.
                    <Card>
                        <CardContent className="p-6 text-sm text-muted-foreground">
                            Nothing is scheduled yet. Open a bot and add a task on its
                            own screen — it runs on that bot&apos;s prompt and tools, so
                            that is where it is set up.
                        </CardContent>
                    </Card>
                ) : (
                    <ul className="space-y-2">
                        {routines.map((r) => (
                            <li key={r.id}>
                                <Link
                                    href={`/workflow/${r.workflow_id}`}
                                    className="flex items-start gap-3 rounded-lg border border-border bg-card p-4 hover:bg-accent"
                                >
                                    <CalendarClock
                                        aria-hidden
                                        className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
                                    />
                                    <span className="min-w-0 flex-1">
                                        <span className="flex flex-wrap items-baseline gap-x-2">
                                            <span className="font-medium">{r.name}</span>
                                            {/* The bot, because a time with no name
                                                beside it is not an answer. */}
                                            <span className="text-sm text-muted-foreground">
                                                {r.workflow_name ?? "bot deleted"}
                                            </span>
                                        </span>
                                        {r.schedule_summary && (
                                            <span className="mt-0.5 block text-sm text-muted-foreground">
                                                {r.schedule_summary}
                                            </span>
                                        )}
                                    </span>
                                    <span
                                        className={
                                            r.is_active
                                                ? "text-sm font-medium text-emerald-600"
                                                : "text-sm text-muted-foreground"
                                        }
                                    >
                                        {r.is_active ? "On" : "Off"}
                                    </span>
                                </Link>
                            </li>
                        ))}
                    </ul>
                )}
            </PageBody>
        </>
    );
}
