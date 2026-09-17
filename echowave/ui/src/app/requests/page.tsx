/**
 * The office's task board (KAN-140 P1).
 *
 * One surface people and bots both work from. A bot that needs a
 * colleague's help files a task; the colleague runs it and the result
 * lands on the card; the bot that asked is told in its own chat. A person
 * files one the same way, for a bot or for the team, and moves the team's
 * cards by hand. Four columns and nothing else to learn.
 */

"use client";

import { Bot, Loader2, Plus, RotateCcw, Trash2, Users } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    createTaskApiV1TasksPost,
    deleteTaskApiV1TasksTaskIdDelete,
    listTasksApiV1TasksGet,
    setTaskStatusApiV1TasksTaskIdStatusPost,
} from "@/client/sdk.gen";
import { HOME_TABS } from "@/components/home/tabs";
import { PageHeader } from "@/components/layout/PageHeader";
import SpinLoader from "@/components/SpinLoader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type Task = {
    id: number;
    title: string;
    brief: string;
    status: string;
    from_workflow_id: number | null;
    from_name: string | null;
    assignee_workflow_id: number | null;
    assignee_name: string | null;
    due_at: string | null;
    result: string | null;
    workflow_run_id: number | null;
    created_at: string | null;
};

type BotRow = { id: number; name: string; handle: string | null };

const COLUMNS: { id: string; label: string; hint: string }[] = [
    // Scheduled is not a status in the database and should not be: a task
    // filed for Tuesday *is* to-do, and on Tuesday it is to-do that day.
    // It is the same rows read by their due date, which is what somebody
    // scanning the board wants -- what is waiting for a date, and what is
    // waiting for them.
    { id: "scheduled", label: "Scheduled", hint: "Filed for a date still ahead" },
    { id: "todo", label: "To do", hint: "Filed, nothing holding it" },
    { id: "doing", label: "Doing", hint: "A bot is on it" },
    { id: "waiting", label: "Waiting", hint: "Needs a person or a retry" },
    { id: "done", label: "Done", hint: "With the result" },
];

/** A task filed for a date that has not arrived. */
function isScheduled(task: Task, now: number): boolean {
    if (task.status !== "todo" || !task.due_at) return false;
    const due = new Date(task.due_at).getTime();
    return Number.isFinite(due) && due > now;
}

function when(iso: string | null): string {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export default function TasksPage() {
    const { user, loading: authLoading, redirectToLogin } = useAuth();
    const hasFetched = useRef(false);
    const [tasks, setTasks] = useState<Task[]>([]);
    const [bots, setBots] = useState<BotRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<number | null>(null);

    const [title, setTitle] = useState("");
    const [brief, setBrief] = useState("");
    const [assignee, setAssignee] = useState("team");
    const [due, setDue] = useState("");
    const [filing, setFiling] = useState(false);

    useEffect(() => {
        if (!authLoading && !user) redirectToLogin();
    }, [authLoading, user, redirectToLogin]);

    const reload = useCallback(async () => {
        const result = await listTasksApiV1TasksGet();
        if (result.error) {
            setError(detailFromResult(result, "Could not load the board"));
            return;
        }
        const data = result.data as { tasks?: Task[]; bots?: BotRow[] } | undefined;
        setTasks(data?.tasks ?? []);
        setBots(data?.bots ?? []);
    }, []);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void reload().then(() => setLoading(false));
        // A bot finishing a task is not a thing this screen is told about;
        // a slow poll keeps the board honest without a socket.
        const timer = setInterval(() => void reload(), 15_000);
        return () => clearInterval(timer);
    }, [authLoading, user, reload]);

    const file = async () => {
        if (!title.trim()) return;
        setFiling(true);
        setError(null);
        const result = await createTaskApiV1TasksPost({
            body: { title, brief, assignee, due: due || null },
        });
        setFiling(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not file the task"));
            return;
        }
        setTitle("");
        setBrief("");
        setDue("");
        await reload();
    };

    const move = async (task: Task, status: string) => {
        setBusy(task.id);
        setError(null);
        const result = await setTaskStatusApiV1TasksTaskIdStatusPost({
            path: { task_id: task.id },
            body: { status },
        });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not move the task"));
            return;
        }
        await reload();
    };

    const remove = async (task: Task) => {
        if (!window.confirm(`Remove "${task.title}" from the board?`)) return;
        setBusy(task.id);
        const result = await deleteTaskApiV1TasksTaskIdDelete({ path: { task_id: task.id } });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not remove the task"));
            return;
        }
        await reload();
    };

    if (authLoading || loading) return <SpinLoader />;

    const now = Date.now();
    const inColumn = (id: string) => {
        if (id === "done") {
            return tasks.filter((t) => t.status === "done" || t.status === "could_not");
        }
        if (id === "scheduled") return tasks.filter((t) => isScheduled(t, now));
        if (id === "todo") {
            return tasks.filter((t) => t.status === "todo" && !isScheduled(t, now));
        }
        return tasks.filter((t) => t.status === id);
    };

    return (
        <>
        {/* The board is a tab of Home, beside the conversation: what the bots
            and the team were handed is the other half of what happened. */}
        <PageHeader
            title="Decibyl"
            description="What the bots and the team have been handed, and what came of it. A bot files a task for a colleague or for you; you file one for a bot or for the team."
            tabs={HOME_TABS}
        />
        <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">

            {error && (
                <div className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}

            <Card className="mt-6">
                <CardContent className="grid gap-3 p-5 sm:grid-cols-[1fr_1fr_auto_auto]">
                    <div className="sm:col-span-2">
                        <Label htmlFor="task-title">New task</Label>
                        <Input
                            id="task-title"
                            className="mt-1"
                            placeholder="Follow up Mrs Lakshmi about Tuesday"
                            value={title}
                            onChange={(e) => setTitle(e.target.value)}
                        />
                    </div>
                    <div>
                        <Label htmlFor="task-assignee">For</Label>
                        <select
                            id="task-assignee"
                            className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                            value={assignee}
                            onChange={(e) => setAssignee(e.target.value)}
                        >
                            <option value="team">The team</option>
                            {bots.map((b) => (
                                <option key={b.id} value={b.handle ? `@${b.handle}` : b.name}>
                                    {b.handle ? `@${b.handle}` : b.name}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <Label htmlFor="task-due">Due</Label>
                        <Input
                            id="task-due"
                            className="mt-1"
                            placeholder="in 3 days"
                            value={due}
                            onChange={(e) => setDue(e.target.value)}
                        />
                    </div>
                    <div className="sm:col-span-3">
                        <Label htmlFor="task-brief">Brief</Label>
                        <Textarea
                            id="task-brief"
                            className="mt-1"
                            rows={2}
                            placeholder="Everything they need to do it without asking you."
                            value={brief}
                            onChange={(e) => setBrief(e.target.value)}
                        />
                    </div>
                    <div className="flex items-end">
                        <Button disabled={filing || !title.trim()} onClick={() => void file()}>
                            {filing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                            File it
                        </Button>
                    </div>
                </CardContent>
            </Card>

            <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-5">
                {COLUMNS.map((column) => (
                    <section key={column.id} aria-label={column.label} className="min-w-0">
                        <h2 className="text-sm font-semibold">
                            {column.label}
                            <span className="ml-2 text-xs font-normal text-muted-foreground">{inColumn(column.id).length}</span>
                        </h2>
                        <p className="text-xs text-muted-foreground">{column.hint}</p>
                        <ul className="mt-3 space-y-3">
                            {inColumn(column.id).map((task) => (
                                <li key={task.id} className="rounded-lg border border-border bg-card p-3 text-sm">
                                    <p className="font-medium">{task.title}</p>
                                    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                                        <span className="inline-flex items-center gap-1">
                                            {task.assignee_name ? <Bot className="h-3 w-3" aria-hidden /> : <Users className="h-3 w-3" aria-hidden />}
                                            {task.assignee_name ?? "The team"}
                                        </span>
                                        {task.from_name && <span>· from {task.from_name}</span>}
                                        {task.due_at && <span>· due {when(task.due_at)}</span>}
                                        {task.status === "could_not" && (
                                            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-900">
                                                Could not
                                            </span>
                                        )}
                                    </p>
                                    {task.brief && <p className="mt-2 whitespace-pre-wrap text-muted-foreground">{task.brief}</p>}
                                    {task.result && (
                                        <p className={cn("mt-2 whitespace-pre-wrap border-l-2 pl-2", task.status === "done" ? "border-emerald-400" : "border-amber-400")}>
                                            {task.result}
                                        </p>
                                    )}
                                    <div className="mt-3 flex flex-wrap items-center gap-1.5">
                                        {task.status === "todo" && !task.assignee_workflow_id && (
                                            <Button size="sm" variant="outline" disabled={busy === task.id} onClick={() => void move(task, "doing")}>
                                                Start
                                            </Button>
                                        )}
                                        {(task.status === "todo" || task.status === "doing" || task.status === "waiting") && !task.assignee_workflow_id && (
                                            <Button size="sm" disabled={busy === task.id} onClick={() => void move(task, "done")}>
                                                Done
                                            </Button>
                                        )}
                                        {(task.status === "waiting" || task.status === "could_not") && task.assignee_workflow_id && (
                                            <Button size="sm" variant="outline" disabled={busy === task.id} onClick={() => void move(task, "todo")}>
                                                <RotateCcw className="mr-1 h-3.5 w-3.5" aria-hidden />
                                                Run again
                                            </Button>
                                        )}
                                        {task.status === "doing" && task.assignee_workflow_id && (
                                            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                                                <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> Working
                                            </span>
                                        )}
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            className="ml-auto text-muted-foreground"
                                            aria-label={`Remove ${task.title}`}
                                            disabled={busy === task.id}
                                            onClick={() => void remove(task)}
                                        >
                                            <Trash2 className="h-3.5 w-3.5" aria-hidden />
                                        </Button>
                                    </div>
                                </li>
                            ))}
                            {inColumn(column.id).length === 0 && (
                                <li className="rounded-lg border border-dashed border-border p-3 text-xs text-muted-foreground">Nothing here.</li>
                            )}
                        </ul>
                    </section>
                ))}
            </div>
        </div>
        </>
    );
}
