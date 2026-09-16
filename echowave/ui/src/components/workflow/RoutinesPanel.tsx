"use client";

/**
 * What a bot does on its own, on a schedule.
 *
 * The runtime shipped without a screen: `agent_routines`, the minute tick,
 * the runner and six endpoints all existed and the only way to create a
 * routine was to write SQL. So the one thing that makes a bot a colleague
 * rather than a phone line -- it does the morning sweep without being
 * asked -- could not be set up by the person who wanted it.
 *
 * Three things this screen refuses to hide:
 *
 * **Arming needs a test run.** The first time a bot runs unsupervised it
 * writes into somebody's real accounting software. The server refuses to
 * arm an untested routine; the switch here says so rather than failing.
 *
 * **A skipped run is shown.** The tick records why it declined -- out of
 * hours, a connector down, do-not-call -- and a routine that silently did
 * not run is indistinguishable from one that never existed.
 *
 * **Next run is the server's answer, not ours.** It is computed from the
 * schedule and the business's opening hours; a screen that recomputed it
 * would eventually disagree with the tick that actually fires.
 */

import { AlertTriangle, Clock, Loader2, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    createRoutineApiV1WorkflowsWorkflowIdRoutinesPost,
    deleteRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdDelete,
    listRoutinesApiV1WorkflowsWorkflowIdRoutinesGet,
    setActiveApiV1WorkflowsWorkflowIdRoutinesRoutineIdActivePost,
    testRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdTestPost,
    updateRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdPut,
} from "@/client/sdk.gen";
import type { Anchor, Cadence, RoutineResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

/** The four cadences, in the words the person setting one would use. Not
 *  cron: the difference between two cron lines is a support ticket. */
const CADENCES: { value: Cadence; label: string }[] = [
    { value: "hourly", label: "Every hour it is open" },
    { value: "daily", label: "Every day" },
    { value: "weekdays", label: "Monday to Friday" },
    { value: "weekly", label: "Once a week" },
];

const ANCHORS: { value: Anchor; label: string }[] = [
    { value: "opening", label: "when it opens" },
    { value: "closing", label: "when it closes" },
    { value: "clock", label: "at a set time" },
];

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function when(iso: string | null | undefined): string {
    if (!iso) return "";
    return new Date(iso).toLocaleString(undefined, {
        weekday: "short",
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function timeToMinute(value: string): number {
    const [h, m] = value.split(":").map((n) => Number.parseInt(n, 10));
    if (!Number.isFinite(h) || !Number.isFinite(m)) return 0;
    return Math.min(24 * 60 - 1, Math.max(0, h * 60 + m));
}

/** The inverse, for filling the form from a routine being edited. Clamped
 *  the same way, so a stored minute outside the day cannot render "25:00"
 *  into a time input that would then refuse to submit. */
function minuteToTime(minute: number): string {
    const safe = Math.min(24 * 60 - 1, Math.max(0, Math.trunc(minute) || 0));
    const h = String(Math.floor(safe / 60)).padStart(2, "0");
    const m = String(safe % 60).padStart(2, "0");
    return `${h}:${m}`;
}

export function RoutinesPanel({ workflowId }: { workflowId: number }) {
    const { user, loading: authLoading } = useAuth();
    const [routines, setRoutines] = useState<RoutineResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [failed, setFailed] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<number | null>(null);
    const [note, setNote] = useState<string | null>(null);

    const [adding, setAdding] = useState(false);
    /**
     * The routine the form is editing, or null when it is creating one.
     *
     * The whole routine, not its id, because two of its fields are not on
     * this form -- ``needs_apps`` and ``offset_minutes`` -- and the endpoint
     * takes the same complete body as create. Sending the form's values
     * alone would clear both, so a routine that ran half an hour before
     * closing and required Sheets would quietly become one that runs at the
     * anchor and requires nothing.
     */
    const [editing, setEditing] = useState<RoutineResponse | null>(null);
    const [name, setName] = useState("");
    const [instruction, setInstruction] = useState("");
    const [cadence, setCadence] = useState<Cadence>("daily");
    const [anchor, setAnchor] = useState<Anchor>("opening");
    const [time, setTime] = useState("09:00");
    const [weekday, setWeekday] = useState(0);
    const [saving, setSaving] = useState(false);

    const load = useCallback(async () => {
        const result = await listRoutinesApiV1WorkflowsWorkflowIdRoutinesGet({
            path: { workflow_id: workflowId },
        });
        if (result.error) {
            setFailed(true);
            return;
        }
        setFailed(false);
        setRoutines(result.data?.routines ?? []);
    }, [workflowId]);

    useEffect(() => {
        if (authLoading || !user) return;
        void load().then(() => setLoading(false));
    }, [authLoading, user, load]);

    /** Put the form back to empty, whichever way it was opened. */
    const closeForm = () => {
        setName("");
        setInstruction("");
        setCadence("daily");
        setAnchor("opening");
        setTime("09:00");
        setWeekday(0);
        setAdding(false);
        setEditing(null);
    };

    const beginEdit = (routine: RoutineResponse) => {
        setEditing(routine);
        setAdding(true);
        setError(null);
        setName(routine.name);
        setInstruction(routine.instruction ?? "");
        setCadence(routine.cadence as Cadence);
        setAnchor(routine.anchor as Anchor);
        setTime(minuteToTime(routine.at_minute));
        setWeekday(routine.weekday);
    };

    const save = async () => {
        if (!name.trim()) return;
        setSaving(true);
        setError(null);
        const body = {
            name: name.trim(),
            instruction: instruction.trim(),
            cadence,
            anchor,
            at_minute: timeToMinute(time),
            // Carried from the routine being edited, never from the form:
            // neither is on it, and the endpoint takes a whole routine. A
            // zero here would move a run that fires half an hour before
            // closing, and an empty list would drop the connectors it cannot
            // work without -- both silently, on a save about the time.
            offset_minutes: editing?.offset_minutes ?? 0,
            weekday,
            needs_apps: editing?.needs_apps ?? [],
        };
        const result = editing
            ? await updateRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdPut({
                  path: { workflow_id: workflowId, routine_id: editing.id },
                  body,
              })
            : await createRoutineApiV1WorkflowsWorkflowIdRoutinesPost({
                  path: { workflow_id: workflowId },
                  body,
              });
        setSaving(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not save that routine"));
            return;
        }
        closeForm();
        await load();
    };

    const test = async (routine: RoutineResponse) => {
        setBusy(routine.id);
        setError(null);
        setNote(null);
        const result = await testRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdTestPost({
            path: { workflow_id: workflowId, routine_id: routine.id },
        });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not start the test run"));
            return;
        }
        setNote(result.data?.detail || `${routine.name} is running once now.`);
        await load();
    };

    const arm = async (routine: RoutineResponse, active: boolean) => {
        setBusy(routine.id);
        setError(null);
        const result = await setActiveApiV1WorkflowsWorkflowIdRoutinesRoutineIdActivePost({
            path: { workflow_id: workflowId, routine_id: routine.id },
            query: { active },
        });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not change that"));
            return;
        }
        await load();
    };

    const remove = async (routine: RoutineResponse) => {
        if (!window.confirm(`Delete "${routine.name}"? It stops running.`)) return;
        setBusy(routine.id);
        const result = await deleteRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdDelete({
            path: { workflow_id: workflowId, routine_id: routine.id },
        });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not delete that routine"));
            return;
        }
        await load();
    };

    if (loading) return null;

    return (
        <section className="space-y-3" data-testid="routines-panel">
            <div className="flex items-baseline justify-between gap-3">
                <div>
                    <h2 className="text-base font-semibold">On a schedule</h2>
                    <p className="text-sm text-muted-foreground">
                        What this bot does on its own, without anybody asking. It follows
                        your opening hours.
                    </p>
                </div>
                {!adding && (
                    <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
                        <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />
                        Add a routine
                    </Button>
                )}
            </div>

            {failed ? (
                <p className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
                    Its routines could not be read just now.
                </p>
            ) : null}

            {adding && (
                <div className="space-y-3 rounded-lg border border-border bg-card p-4">
                    <div>
                        <Label htmlFor="routine-name">Call it</Label>
                        <Input
                            id="routine-name"
                            className="mt-1"
                            placeholder="Morning summary"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                        />
                    </div>
                    <div>
                        <Label htmlFor="routine-instruction">What it should do</Label>
                        <Textarea
                            id="routine-instruction"
                            className="mt-1"
                            rows={2}
                            placeholder="Read yesterday's calls and message me anything that needs an answer."
                            value={instruction}
                            onChange={(e) => setInstruction(e.target.value)}
                        />
                    </div>
                    <div className="grid gap-3 sm:grid-cols-3">
                        <div>
                            <Label htmlFor="routine-cadence">How often</Label>
                            <select
                                id="routine-cadence"
                                className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                                value={cadence}
                                onChange={(e) => setCadence(e.target.value as Cadence)}
                            >
                                {CADENCES.map((c) => (
                                    <option key={c.value} value={c.value}>
                                        {c.label}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <Label htmlFor="routine-anchor">When</Label>
                            <select
                                id="routine-anchor"
                                className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                                value={anchor}
                                onChange={(e) => setAnchor(e.target.value as Anchor)}
                            >
                                {ANCHORS.map((a) => (
                                    <option key={a.value} value={a.value}>
                                        {a.label}
                                    </option>
                                ))}
                            </select>
                        </div>
                        {anchor === "clock" ? (
                            <div>
                                <Label htmlFor="routine-time">Time</Label>
                                <Input
                                    id="routine-time"
                                    type="time"
                                    className="mt-1"
                                    value={time}
                                    onChange={(e) => setTime(e.target.value)}
                                />
                            </div>
                        ) : null}
                        {cadence === "weekly" ? (
                            <div>
                                <Label htmlFor="routine-weekday">Day</Label>
                                <select
                                    id="routine-weekday"
                                    className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                                    value={weekday}
                                    onChange={(e) => setWeekday(Number.parseInt(e.target.value, 10))}
                                >
                                    {DAYS.map((d, i) => (
                                        <option key={d} value={i}>
                                            {d}
                                        </option>
                                    ))}
                                </select>
                            </div>
                        ) : null}
                    </div>
                    <div className="flex items-center gap-2">
                        <Button size="sm" disabled={saving || !name.trim()} onClick={() => void save()}>
                            {saving ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                            {editing ? "Save changes" : "Save it"}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={closeForm}>
                            Cancel
                        </Button>
                        <span className="text-xs text-muted-foreground">
                            {editing
                                ? "Changing the schedule does not un-run the test."
                                : "It stays off until you have run it once."}
                        </span>
                    </div>
                </div>
            )}

            {error ? (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            ) : null}
            {note ? <p className="text-sm text-emerald-700 dark:text-emerald-300">{note}</p> : null}

            <ul className="space-y-2">
                {routines.map((routine) => (
                    <li
                        key={routine.id}
                        className="rounded-lg border border-border bg-card p-4"
                        data-testid="routine-row"
                    >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0">
                                <p className="text-sm font-medium">
                                    {routine.name}
                                    {routine.is_active ? (
                                        <span className="ml-2 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-900">
                                            On
                                        </span>
                                    ) : (
                                        <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                            Off
                                        </span>
                                    )}
                                </p>
                                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                                    <span className="inline-flex items-center gap-1">
                                        <Clock className="h-3 w-3" aria-hidden />
                                        {routine.schedule_summary}
                                    </span>
                                    {routine.next_run_at ? (
                                        <span>· next {when(routine.next_run_at)}</span>
                                    ) : routine.is_active ? (
                                        <span>· nothing due in the next fortnight</span>
                                    ) : null}
                                    {routine.last_fired_at ? (
                                        <span>· last ran {when(routine.last_fired_at)}</span>
                                    ) : null}
                                </p>
                                {routine.instruction ? (
                                    <p className="mt-1 text-sm text-muted-foreground">
                                        {routine.instruction}
                                    </p>
                                ) : null}
                                {/* A routine that silently did not run is
                                    indistinguishable from one that never
                                    existed, so the tick's reason is shown. */}
                                {routine.last_skipped_reason ? (
                                    <p className="mt-1 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
                                        <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                                        <span>
                                            Did not run {when(routine.last_skipped_at)}:{" "}
                                            {routine.last_skipped_reason}
                                        </span>
                                    </p>
                                ) : null}
                            </div>
                            <div className="flex shrink-0 items-center gap-1.5">
                                <Button
                                    size="sm"
                                    variant="outline"
                                    disabled={busy === routine.id}
                                    onClick={() => void test(routine)}
                                >
                                    <Play className="mr-1 h-3.5 w-3.5" aria-hidden />
                                    Run once
                                </Button>
                                <Button
                                    size="sm"
                                    variant={routine.is_active ? "outline" : "default"}
                                    disabled={busy === routine.id || (!routine.is_active && !routine.may_arm)}
                                    title={
                                        !routine.is_active && !routine.may_arm
                                            ? "Run it once first. The first unsupervised run is not the one to find a mistake in."
                                            : undefined
                                    }
                                    onClick={() => void arm(routine, !routine.is_active)}
                                >
                                    {routine.is_active ? "Switch off" : "Switch on"}
                                </Button>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    aria-label={`Edit ${routine.name}`}
                                    disabled={busy === routine.id}
                                    onClick={() => beginEdit(routine)}
                                >
                                    <Pencil className="h-3.5 w-3.5" aria-hidden />
                                </Button>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    className="text-muted-foreground"
                                    aria-label={`Delete ${routine.name}`}
                                    disabled={busy === routine.id}
                                    onClick={() => void remove(routine)}
                                >
                                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                                </Button>
                            </div>
                        </div>
                        {!routine.is_active && !routine.may_arm ? (
                            <p className="mt-2 text-xs text-muted-foreground">
                                Run it once before switching it on.
                            </p>
                        ) : null}
                    </li>
                ))}
            </ul>

            {routines.length === 0 && !adding && !failed ? (
                <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
                    Nothing scheduled. A routine is the bot doing its job without being
                    asked: a morning summary, an end-of-day sweep, a Monday chase.
                </p>
            ) : null}
        </section>
    );
}
