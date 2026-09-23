/**
 * One task on its own page (TB-2), the way paperclip opens an issue.
 *
 * The main column is the work: the brief, the agent's latest report, the
 * sub-tasks, and one thread where people's lines, agents' reports and the
 * board's own record (who moved it, who it was handed to) read in order.
 * Naming an agent there -- "@billing can you chase this?" -- hands it the
 * task and runs it with the thread in front of it. The side column is the
 * task's properties, each changed where it is shown.
 */

"use client";

import { ArrowLeft, ExternalLink, Loader2, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
    addCommentApiV1TasksTaskIdCommentsPost,
    deleteTaskApiV1TasksTaskIdDelete,
    editTaskApiV1TasksTaskIdPatch,
    getTaskApiV1TasksTaskIdGet,
    listTasksApiV1TasksGet,
    setTaskStatusApiV1TasksTaskIdStatusPost,
} from "@/client/sdk.gen";
import SpinLoader from "@/components/SpinLoader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";
import { cn } from "@/lib/utils";

import { LiveDot, NewTaskDialog, StatusDot } from "./TaskBoard";
import { LabelChip, LabelPicker, MentionTextarea } from "./TaskInputs";
import { type BoardPayload, COLUMNS, type Comment, isLive, ownerValue, parseWaitsOn, type Task, when } from "./tasks";

type Detail = Task & { comments?: Comment[]; subtasks?: Task[] };

const FIELD = "mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm";

export function TaskPage({ taskId }: { taskId: number }) {
    const router = useRouter();
    const [task, setTask] = useState<Detail | null | undefined>(undefined);
    const [board, setBoard] = useState<BoardPayload>({});
    const [error, setError] = useState<string | null>(null);
    const [line, setLine] = useState("");
    const [saving, setSaving] = useState(false);
    const [addingSub, setAddingSub] = useState(false);
    const [waitsOn, setWaitsOn] = useState("");

    const load = useCallback(async () => {
        const [one, all] = await Promise.all([getTaskApiV1TasksTaskIdGet({ path: { task_id: taskId } }), listTasksApiV1TasksGet()]);
        if (one.error) {
            setTask(null);
            setError(detailFromResult(one, "That task is not here"));
            return;
        }
        const detail = one.data as Detail;
        setTask(detail);
        if (!all.error) setBoard((all.data as BoardPayload | undefined) ?? {});
        const known = ((all.data as BoardPayload | undefined)?.tasks ?? []) as Task[];
        setWaitsOn(detail.blocked_by.map((id) => known.find((t) => t.id === id)?.identifier ?? String(id)).join(", "));
    }, [taskId]);

    useEffect(() => {
        void load();
    }, [load]);

    const live = task ? isLive(task) : false;
    useEffect(() => {
        // While an agent works it, its report and the dot follow without a
        // refresh.
        if (!live) return;
        const timer = setInterval(() => void load(), 5_000);
        return () => clearInterval(timer);
    }, [live, load]);

    if (task === undefined) return <SpinLoader />;
    if (task === null)
        return (
            <div className="mx-auto max-w-3xl px-4 py-10 text-sm">
                <p>{error ?? "That task is not here."}</p>
                <Link href="/tasks" className="mt-3 inline-block underline">
                    Back to the board
                </Link>
            </div>
        );

    const tasks = board.tasks ?? [];
    const bots = board.bots ?? [];
    const people = board.board?.people ?? [];
    const priorities = board.board?.priorities ?? ["critical", "high", "medium", "low"];
    const knownLabels = board.board?.labels ?? [];
    const parent = task.parent_id ? tasks.find((t) => t.id === task.parent_id) : null;
    const blockers = task.blocked_by.map((id) => tasks.find((t) => t.id === id)).filter(Boolean) as Task[];

    const run = async <T,>(call: () => Promise<{ error?: unknown } & T>, failure: string) => {
        setSaving(true);
        setError(null);
        const result = await call();
        setSaving(false);
        if (result.error) {
            setError(detailFromResult(result as Parameters<typeof detailFromResult>[0], failure));
            return false;
        }
        await load();
        return true;
    };

    const edit = (changes: Record<string, unknown>) => run(() => editTaskApiV1TasksTaskIdPatch({ path: { task_id: task.id }, body: changes }), "Could not change the task");
    const move = (status: string) => run(() => setTaskStatusApiV1TasksTaskIdStatusPost({ path: { task_id: task.id }, body: { status } }), "Could not move the task");
    const say = async () => {
        if (!line.trim()) return;
        if (await run(() => addCommentApiV1TasksTaskIdCommentsPost({ path: { task_id: task.id }, body: { body: line } }), "Could not add the line")) setLine("");
    };
    const remove = async () => {
        if (!window.confirm(`Remove ${task.identifier ?? task.title} from the board?`)) return;
        const result = await deleteTaskApiV1TasksTaskIdDelete({ path: { task_id: task.id } });
        if (result.error) {
            setError(detailFromResult(result, "Could not remove the task"));
            return;
        }
        router.push("/tasks");
    };

    return (
        <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <Link href="/tasks" className="inline-flex items-center gap-1 hover:text-foreground">
                    <ArrowLeft className="h-4 w-4" aria-hidden /> Tasks
                </Link>
                {parent && (
                    <>
                        <span>/</span>
                        <Link href={`/tasks/${parent.id}`} className="hover:text-foreground">
                            {parent.identifier ?? parent.title}
                        </Link>
                    </>
                )}
                <span>/</span>
                <span className="font-mono">{task.identifier}</span>
            </div>

            <h1 className="mt-3 flex items-center gap-3 text-xl font-semibold tracking-tight">
                <StatusDot status={task.status} />
                {task.title}
                {live && <LiveDot />}
            </h1>
            {(task.labels ?? []).length > 0 && (
                <p className="mt-2 flex flex-wrap gap-1">
                    {task.labels!.map((l) => (
                        <LabelChip key={l} label={l} />
                    ))}
                </p>
            )}
            {error && (
                <p role="alert" className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                    {error}
                </p>
            )}

            <div className="mt-6 grid gap-8 lg:grid-cols-[1fr_280px]">
                <main className="min-w-0 space-y-6">
                    {task.brief && <p className="whitespace-pre-wrap text-sm">{task.brief}</p>}

                    {task.result && (
                        <section>
                            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Latest report</h2>
                            <p className={cn("mt-1 whitespace-pre-wrap border-l-2 pl-3 text-sm", task.status === "blocked" ? "border-amber-400" : "border-emerald-400")}>{task.result}</p>
                            {task.status === "in_review" && (
                                <div className="mt-3 flex gap-2">
                                    <Button size="sm" disabled={saving} onClick={() => void move("done")}>
                                        Sign off
                                    </Button>
                                    <Button size="sm" variant="outline" disabled={saving} onClick={() => void move("todo")}>
                                        Send back
                                    </Button>
                                </div>
                            )}
                        </section>
                    )}
                    {task.status === "blocked" && task.assignee_workflow_id && (
                        <Button size="sm" variant="outline" disabled={saving} onClick={() => void move("todo")}>
                            Run again
                        </Button>
                    )}

                    <section aria-label="Sub-tasks">
                        <div className="flex items-center justify-between">
                            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Sub-tasks</h2>
                            <Button size="sm" variant="ghost" onClick={() => setAddingSub(true)}>
                                <Plus className="mr-1 h-3.5 w-3.5" aria-hidden /> Add sub-task
                            </Button>
                        </div>
                        {(task.subtasks ?? []).length === 0 ? (
                            <p className="text-xs text-muted-foreground">None. Break the work down here and each piece gets its own owner.</p>
                        ) : (
                            <ul className="mt-1 divide-y rounded-md border border-border">
                                {task.subtasks!.map((s) => (
                                    <li key={s.id}>
                                        <Link href={`/tasks/${s.id}`} className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-muted/50">
                                            <StatusDot status={s.status} />
                                            <span className="font-mono text-xs text-muted-foreground">{s.identifier}</span>
                                            <span className="min-w-0 flex-1 truncate">{s.title}</span>
                                            {isLive(s) && <LiveDot />}
                                        </Link>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </section>

                    <section aria-label="Thread">
                        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Thread</h2>
                        <ol className="mt-2 space-y-3">
                            <li className="text-xs text-muted-foreground">
                                {task.from_name ? `${task.from_name} filed this` : "Filed by a person"} · {when(task.created_at)}
                            </li>
                            {(task.comments ?? []).map((c) =>
                                c.author_user_id || c.author_workflow_id ? (
                                    <li key={c.id} className="rounded-md border border-border p-3 text-sm">
                                        <p className="text-xs text-muted-foreground">
                                            <span className="font-medium text-foreground">{c.author_name ?? (c.author_workflow_id ? "An agent" : "A person")}</span>
                                            {c.created_at && ` · ${when(c.created_at)}`}
                                        </p>
                                        <p className="mt-1 whitespace-pre-wrap">{c.body}</p>
                                    </li>
                                ) : (
                                    <li key={c.id} className="text-xs text-muted-foreground">
                                        {c.body} {c.created_at && `· ${when(c.created_at)}`}
                                    </li>
                                ),
                            )}
                            {task.started_at && <li className="text-xs text-muted-foreground">Work started · {when(task.started_at)}</li>}
                            {task.finished_at && <li className="text-xs text-muted-foreground">Finished · {when(task.finished_at)}</li>}
                        </ol>
                        <div className="mt-3 space-y-2">
                            <MentionTextarea
                                aria-label="Add to the thread"
                                rows={3}
                                placeholder="Add a line. Type @ to hand the task to an agent and run it now."
                                value={line}
                                onChange={setLine}
                                bots={bots}
                                onSubmit={() => void say()}
                            />
                            <div className="flex items-center justify-between gap-2">
                                <p className="text-xs text-muted-foreground">Ctrl+Enter sends</p>
                                <Button size="sm" disabled={saving || !line.trim()} onClick={() => void say()}>
                                    {saving ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
                                    Send
                                </Button>
                            </div>
                        </div>
                    </section>
                </main>

                <aside aria-label="Properties" className="space-y-4 text-sm">
                    <div>
                        <Label htmlFor="task-status">Status</Label>
                        <select id="task-status" className={FIELD} value={task.status} disabled={saving} onChange={(e) => void move(e.target.value)}>
                            {COLUMNS.map((c) => (
                                <option key={c.id} value={c.id}>{c.label}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <Label htmlFor="task-priority">Priority</Label>
                        <select id="task-priority" className={FIELD} value={task.priority} disabled={saving} onChange={(e) => void edit({ priority: e.target.value })}>
                            {priorities.map((p) => (
                                <option key={p} value={p}>{p}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <Label htmlFor="task-owner">Owner</Label>
                        <select id="task-owner" className={FIELD} value={ownerValue(task, bots)} disabled={saving} onChange={(e) => void edit({ assignee: e.target.value })}>
                            <option value="team">The team</option>
                            {people.map((p) => (
                                <option key={`u${p.id}`} value={`user:${p.id}`}>{p.name}</option>
                            ))}
                            {bots.map((b) => (
                                <option key={`b${b.id}`} value={b.handle ? `@${b.handle}` : b.name}>{b.handle ? `@${b.handle}` : b.name}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <Label htmlFor="task-labels">Labels</Label>
                        <LabelPicker id="task-labels" value={task.labels ?? []} known={knownLabels} disabled={saving} onChange={(labels) => void edit({ labels })} />
                    </div>
                    <div>
                        <Label htmlFor="task-parent">Part of</Label>
                        <select id="task-parent" className={FIELD} value={task.parent_id ?? ""} disabled={saving} onChange={(e) => void edit({ parent_id: e.target.value ? Number(e.target.value) : null })}>
                            <option value="">Nothing</option>
                            {tasks
                                .filter((t) => t.id !== task.id)
                                .map((t) => (
                                    <option key={t.id} value={t.id}>{`${t.identifier ?? t.id} ${t.title}`}</option>
                                ))}
                        </select>
                    </div>
                    <div>
                        <Label htmlFor="task-waits">Waits on</Label>
                        <Input id="task-waits" className="mt-1" placeholder="DEC-12, DEC-14" value={waitsOn} onChange={(e) => setWaitsOn(e.target.value)} onBlur={() => void edit({ blocked_by: parseWaitsOn(waitsOn, tasks) })} />
                        {blockers.length > 0 && (
                            <ul className="mt-1 space-y-1">
                                {blockers.map((b) => (
                                    <li key={b.id}>
                                        <Link href={`/tasks/${b.id}`} className="inline-flex items-center gap-2 text-xs hover:underline">
                                            <StatusDot status={b.status} /> {b.identifier} {b.title}
                                        </Link>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                    <div>
                        <Label htmlFor="task-due">Due</Label>
                        <Input id="task-due" className="mt-1" placeholder="in 3 days" defaultValue={task.due_at ? task.due_at.slice(0, 10) : ""} onBlur={(e) => void edit({ due: e.target.value || null })} />
                    </div>
                    {task.workflow_run_id && task.assignee_workflow_id && (
                        <Link href={`/workflow/${task.assignee_workflow_id}/run/${task.workflow_run_id}`} className="inline-flex items-center gap-1 text-xs underline">
                            <ExternalLink className="h-3 w-3" aria-hidden /> The agent&apos;s run
                        </Link>
                    )}
                    <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={() => void remove()}>
                        <Trash2 className="mr-1 h-3.5 w-3.5" aria-hidden /> Remove
                    </Button>
                </aside>
            </div>

            {addingSub && (
                <NewTaskDialog
                    bots={bots}
                    people={people}
                    priorities={priorities}
                    knownLabels={knownLabels}
                    parentId={task.id}
                    onClose={() => setAddingSub(false)}
                    onFiled={async () => {
                        setAddingSub(false);
                        await load();
                    }}
                />
            )}
        </div>
    );
}
