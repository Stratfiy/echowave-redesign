/**
 * Tasks: everything that runs on its own, across every bot.
 *
 * Routines were reachable one bot at a time, which answers "what does this
 * bot do" and never "what runs tomorrow morning" -- the question somebody
 * actually has, and one they could not ask without already knowing which
 * bots to open. So the schedule the business runs on was invisible.
 *
 * It shipped read-only, on the reasoning that a routine belongs beside the
 * bot whose prompt and tools it runs on. That was wrong about half of it: a
 * screen you come to because a task fired at the wrong hour, or fires and
 * should not, is exactly where you change the hour and switch it off. Going
 * to find the bot first is the work, not the answer.
 *
 * So the schedule is editable here -- when it runs, on, off, gone -- with
 * the same controls the bot's own panel uses, because two spellings of
 * "Monday to Friday" is one screen disagreeing with the next.
 *
 * What it does is not editable here. The instruction only means anything
 * beside that bot's prompt and its connected accounts, and a textarea on
 * this screen would invite somebody to rewrite a job they cannot see the
 * tools for. That link is one press away and says so.
 *
 * Every change is a whole routine, never a patch: the endpoint replaces, so
 * the fields this screen does not show are carried from the row it is
 * editing. A save about the time must not quietly clear the accounts a run
 * waits on.
 */

"use client";

import { CalendarClock, Loader2, Pencil, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
    deleteDecibylRoutineApiV1RoutinesRoutineIdDelete,
    deleteRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdDelete,
    listAllRoutinesApiV1RoutinesGet,
    setActiveApiV1WorkflowsWorkflowIdRoutinesRoutineIdActivePost,
    setDecibylRoutineActiveApiV1RoutinesRoutineIdActivePost,
    testDecibylRoutineApiV1RoutinesRoutineIdTestPost,
    updateRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdPut,
} from "@/client/sdk.gen";
import type { Anchor, Cadence } from "@/client/types.gen";
import { PageBody, PageHeader, type PageTab } from "@/components/layout/PageHeader";
import { DESK_TABS } from "@/components/layout/SectionTabs";
import SpinLoader from "@/components/SpinLoader";
import { RoutineNextRun } from "@/components/today/RoutineNextRun";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ScheduleFields, type ScheduleValue } from "@/components/workflow/ScheduleFields";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { minuteToTime, timeToMinute, when } from "@/lib/schedule";

type Routine = {
    id: number;
    /** null for one of Decibyl's own (KAN-156): no bot, run by the assistant's turn. */
    workflow_id: number | null;
    workflow_name?: string | null;
    name: string;
    instruction: string;
    cadence: string;
    anchor: string;
    at_minute: number;
    offset_minutes: number;
    weekday: number;
    needs_apps: string[];
    schedule_summary?: string | null;
    next_run_at?: string | null;
    is_active: boolean;
};

type Props = { tabs?: PageTab[] };

export function SchedulesBoard({ tabs = DESK_TABS }: Props) {
    const { user, loading: authLoading } = useAuth();
    const [routines, setRoutines] = useState<Routine[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    /** The routine whose request is in flight, so only its own buttons go quiet. */
    const [busy, setBusy] = useState<number | null>(null);
    const [editing, setEditing] = useState<number | null>(null);
    const [draft, setDraft] = useState<ScheduleValue | null>(null);
    // Stream today: the next run, said before saving (screen 10).
    const nextRun = useFeature("today_list");

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

    const beginEdit = (routine: Routine) => {
        setError(null);
        setEditing(routine.id);
        setDraft({
            cadence: routine.cadence as Cadence,
            anchor: routine.anchor as Anchor,
            time: minuteToTime(routine.at_minute),
            weekday: routine.weekday,
        });
    };

    const closeEdit = () => {
        setEditing(null);
        setDraft(null);
    };

    const saveTime = async (routine: Routine) => {
        if (!draft) return;
        setBusy(routine.id);
        setError(null);
        if (routine.workflow_id === null) return; // Decibyl's rows have no editor yet
        const result = await updateRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdPut({
            path: { workflow_id: routine.workflow_id, routine_id: routine.id },
            body: {
                // The whole routine. The endpoint replaces rather than
                // patches, so the fields this screen does not show are
                // carried from the row: a save about the hour must not clear
                // the instruction, the offset or the accounts a run needs.
                name: routine.name,
                instruction: routine.instruction,
                offset_minutes: routine.offset_minutes,
                needs_apps: routine.needs_apps ?? [],
                cadence: draft.cadence,
                anchor: draft.anchor,
                at_minute: timeToMinute(draft.time),
                weekday: draft.weekday,
            },
        });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not change when that runs"));
            return;
        }
        closeEdit();
        await load();
    };

    // Decibyl's own routines have no bot, so they are reached on the flat
    // /routines path; a bot's routine stays on its bot's path.
    const isDecibyls = (routine: Routine) => routine.workflow_id === null;

    const testRun = async (routine: Routine) => {
        setBusy(routine.id);
        setError(null);
        const result = await testDecibylRoutineApiV1RoutinesRoutineIdTestPost({
            path: { routine_id: routine.id },
        });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not start the test run"));
            return;
        }
        await load();
    };

    const arm = async (routine: Routine, active: boolean) => {
        setBusy(routine.id);
        setError(null);
        const result = isDecibyls(routine)
            ? await setDecibylRoutineActiveApiV1RoutinesRoutineIdActivePost({
                  path: { routine_id: routine.id },
                  query: { active },
              })
            : await setActiveApiV1WorkflowsWorkflowIdRoutinesRoutineIdActivePost({
                  path: { workflow_id: routine.workflow_id as number, routine_id: routine.id },
                  query: { active },
              });
        setBusy(null);
        if (result.error) {
            // The server refuses to arm a routine that has never been
            // test-run, which is the one message worth passing through
            // whole: it names the thing to go and do.
            setError(detailFromResult(result, "Could not change that"));
            return;
        }
        await load();
    };

    const remove = async (routine: Routine) => {
        if (!window.confirm(`Delete "${routine.name}"? It stops running.`)) return;
        setBusy(routine.id);
        setError(null);
        const result = isDecibyls(routine)
            ? await deleteDecibylRoutineApiV1RoutinesRoutineIdDelete({
                  path: { routine_id: routine.id },
              })
            : await deleteRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdDelete({
                  path: { workflow_id: routine.workflow_id as number, routine_id: routine.id },
              });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not delete that task"));
            return;
        }
        await load();
    };

    return (
        <>
            <PageHeader
                title="Routines"
                description="What runs on its own, and when."
                tabs={tabs}
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
                            Nothing is scheduled yet. Open an agent, go to its Triggers
                            tab and add one under On a schedule — it runs on that
                            agent&apos;s prompt and tools, so that is where it is set up.
                        </CardContent>
                    </Card>
                ) : (
                    <ul className="space-y-2">
                        {routines.map((r) => (
                            <li
                                key={r.id}
                                className="rounded-lg border border-border bg-card p-4"
                                data-testid={`task-${r.id}`}
                            >
                                <div className="flex items-start gap-3">
                                    <CalendarClock
                                        aria-hidden
                                        className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
                                    />
                                    <div className="min-w-0 flex-1">
                                        <div className="flex flex-wrap items-baseline gap-x-2">
                                            <Link
                                                href={isDecibyls(r) ? "/overview" : `/workflow/${r.workflow_id}`}
                                                className="font-medium hover:underline"
                                            >
                                                {r.name}
                                            </Link>
                                            {/* The bot, because a time with no name
                                                beside it is not an answer. */}
                                            <span className="text-sm text-muted-foreground">
                                                {isDecibyls(r) ? "Decibyl" : (r.workflow_name ?? "agent deleted")}
                                            </span>
                                        </div>
                                        {r.schedule_summary && (
                                            <p className="mt-0.5 text-sm text-muted-foreground">
                                                {r.schedule_summary}
                                                {r.is_active && r.next_run_at
                                                    ? ` · next ${when(r.next_run_at)}`
                                                    : ""}
                                            </p>
                                        )}
                                    </div>
                                    <span
                                        className={
                                            r.is_active
                                                ? "text-sm font-medium text-emerald-600"
                                                : "text-sm text-muted-foreground"
                                        }
                                    >
                                        {r.is_active ? "On" : "Off"}
                                    </span>
                                </div>

                                <div className="mt-3 flex flex-wrap items-center gap-2">
                                    {busy === r.id ? (
                                        <Loader2
                                            aria-label="Working"
                                            className="h-4 w-4 animate-spin text-muted-foreground"
                                        />
                                    ) : null}
                                    {isDecibyls(r) ? (
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={busy === r.id}
                                            onClick={() => void testRun(r)}
                                        >
                                            Test run
                                        </Button>
                                    ) : (
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={busy === r.id}
                                            onClick={() =>
                                                editing === r.id ? closeEdit() : beginEdit(r)
                                            }
                                        >
                                            <Pencil className="mr-1 h-3.5 w-3.5" aria-hidden />
                                            {editing === r.id ? "Cancel" : "Change when"}
                                        </Button>
                                    )}
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={busy === r.id}
                                        onClick={() => void arm(r, !r.is_active)}
                                    >
                                        {r.is_active ? "Switch off" : "Switch on"}
                                    </Button>
                                    <Button
                                        size="sm"
                                        variant="ghost"
                                        className="text-destructive hover:text-destructive"
                                        disabled={busy === r.id}
                                        onClick={() => void remove(r)}
                                    >
                                        <Trash2 className="mr-1 h-3.5 w-3.5" aria-hidden />
                                        Delete
                                    </Button>
                                    {/* What it does lives with the bot, and this
                                        says so rather than leaving somebody
                                        hunting for where the wording is kept. */}
                                    {!isDecibyls(r) && (
                                        <Link
                                            href={`/workflow/${r.workflow_id}`}
                                            className="text-sm text-muted-foreground underline"
                                        >
                                            Edit what it does
                                        </Link>
                                    )}
                                </div>

                                {editing === r.id && draft ? (
                                    <div className="mt-3 space-y-3 rounded-md border border-border bg-background p-3">
                                        <ScheduleFields
                                            idPrefix={`task-${r.id}`}
                                            value={draft}
                                            onChange={setDraft}
                                        />
                                        {nextRun && <RoutineNextRun value={draft} offsetMinutes={r.offset_minutes} />}
                                        <div className="flex gap-2">
                                            <Button
                                                size="sm"
                                                disabled={busy === r.id}
                                                onClick={() => void saveTime(r)}
                                            >
                                                Save
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                onClick={closeEdit}
                                            >
                                                Cancel
                                            </Button>
                                        </div>
                                    </div>
                                ) : null}
                            </li>
                        ))}
                    </ul>
                )}
            </PageBody>
        </>
    );
}
