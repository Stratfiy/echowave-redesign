/**
 * The board is a board (TB-1), on paperclip's issue model.
 *
 * Seven columns -- backlog, to do, in progress, in review, done, blocked,
 * cancelled -- one card per task, dragged between them. A card carries its
 * identifier, priority, owner (a bot or a person), due date, what it waits
 * on, and how many lines are on it; opened, it shows the brief, the agent's
 * report and the conversation, and lets a person change any of its fields
 * or move it. An agent's finished task waits in review for a person to sign
 * off; that column is the review queue. The list view stays as a toggle.
 */

"use client";

import { Bot, GripVertical, Loader2, MessageSquare, Plus, Trash2, User, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
    addCommentApiV1TasksTaskIdCommentsPost,
    createTaskApiV1TasksPost,
    deleteTaskApiV1TasksTaskIdDelete,
    editTaskApiV1TasksTaskIdPatch,
    getTaskApiV1TasksTaskIdGet,
    listTasksApiV1TasksGet,
    setTaskStatusApiV1TasksTaskIdStatusPost,
} from "@/client/sdk.gen";
import { PageHeader, type PageTab } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { detailFromResult } from "@/lib/apiError";
import { cn } from "@/lib/utils";

export type Task = {
    id: number;
    number: number | null;
    identifier: string | null;
    title: string;
    brief: string;
    status: string;
    priority: string;
    from_workflow_id: number | null;
    from_name: string | null;
    assignee_workflow_id: number | null;
    assignee_name: string | null;
    assignee_user_id: number | null;
    assignee_user_name: string | null;
    parent_id: number | null;
    blocked_by: number[];
    comment_count: number | null;
    created_by: number | null;
    due_at: string | null;
    result: string | null;
    workflow_run_id: number | null;
    created_at: string | null;
};

export type Comment = {
    id: number;
    body: string;
    author_name: string | null;
    author_workflow_id: number | null;
    author_user_id: number | null;
    created_at: string | null;
};

export type BoardPayload = {
    tasks?: Task[];
    statuses?: string[];
    bots?: { id: number; name: string; handle: string | null }[];
    board?: {
        enabled?: boolean;
        priorities?: string[];
        prefix?: string;
        people?: { id: number; name: string }[];
        me?: number;
    };
};

export const COLUMNS: { id: string; label: string; hint: string }[] = [
    { id: "backlog", label: "Backlog", hint: "Filed, not yet planned" },
    { id: "todo", label: "To do", hint: "Next up" },
    { id: "in_progress", label: "In progress", hint: "Being worked" },
    { id: "in_review", label: "In review", hint: "An agent's report, waiting for you" },
    { id: "done", label: "Done", hint: "Signed off" },
    { id: "blocked", label: "Blocked", hint: "Needs a person or a retry" },
    { id: "cancelled", label: "Cancelled", hint: "Will not be done" },
];

const PRIORITY_CLASS: Record<string, string> = {
    critical: "border-transparent bg-red-100 text-red-900",
    high: "border-transparent bg-amber-100 text-amber-900",
    medium: "border-border/70 bg-background/80 text-muted-foreground",
    low: "border-transparent bg-slate-100 text-slate-700",
};

function when(iso: string | null): string {
    if (!iso) return "";
    return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function ownerOf(task: Task): { icon: typeof Bot; name: string } {
    if (task.assignee_name) return { icon: Bot, name: task.assignee_name };
    if (task.assignee_user_name) return { icon: User, name: task.assignee_user_name };
    return { icon: Users, name: "The team" };
}

type Filters = { owner: string; priority: string; mine: boolean };

type Props = { initial: BoardPayload; tabs: PageTab[] };

export function TaskBoard({ initial, tabs }: Props) {
    const [payload, setPayload] = useState<BoardPayload>(initial);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<number | null>(null);
    const [view, setView] = useState<"board" | "list">("board");
    const [filters, setFilters] = useState<Filters>({ owner: "all", priority: "all", mine: false });
    const [open, setOpen] = useState<number | null>(null);
    const [dragging, setDragging] = useState<number | null>(null);
    const [over, setOver] = useState<string | null>(null);

    const [title, setTitle] = useState("");
    const [brief, setBrief] = useState("");
    const [assignee, setAssignee] = useState("team");
    const [priority, setPriority] = useState("medium");
    const [due, setDue] = useState("");
    const [backlog, setBacklog] = useState(false);
    const [filing, setFiling] = useState(false);

    const tasks = useMemo(() => payload.tasks ?? [], [payload]);
    const bots = payload.bots ?? [];
    const people = payload.board?.people ?? [];
    const priorities = payload.board?.priorities ?? ["critical", "high", "medium", "low"];
    const me = payload.board?.me ?? null;

    const reload = useCallback(async () => {
        const result = await listTasksApiV1TasksGet();
        if (result.error) {
            setError(detailFromResult(result, "Could not load the board"));
            return;
        }
        setPayload((result.data as BoardPayload | undefined) ?? {});
    }, []);

    useEffect(() => {
        // A bot finishing a task is not a thing this screen is told about;
        // a slow poll keeps the board honest without a socket.
        const timer = setInterval(() => void reload(), 15_000);
        return () => clearInterval(timer);
    }, [reload]);

    const move = async (task: Task, status: string) => {
        if (task.status === status) return;
        setBusy(task.id);
        setError(null);
        // Optimistic: the card lands where it was dropped, and comes back if
        // the server says no.
        setPayload((p) => ({ ...p, tasks: (p.tasks ?? []).map((t) => (t.id === task.id ? { ...t, status } : t)) }));
        const result = await setTaskStatusApiV1TasksTaskIdStatusPost({ path: { task_id: task.id }, body: { status } });
        setBusy(null);
        if (result.error) setError(detailFromResult(result, "Could not move the task"));
        await reload();
    };

    const file = async () => {
        if (!title.trim()) return;
        setFiling(true);
        setError(null);
        const result = await createTaskApiV1TasksPost({
            body: { title, brief, assignee, priority, due: due || null, backlog },
        });
        setFiling(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not file the task"));
            return;
        }
        setTitle("");
        setBrief("");
        setDue("");
        setBacklog(false);
        await reload();
    };

    const remove = async (task: Task) => {
        if (!window.confirm(`Remove ${task.identifier ?? task.title} from the board?`)) return;
        setBusy(task.id);
        const result = await deleteTaskApiV1TasksTaskIdDelete({ path: { task_id: task.id } });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not remove the task"));
            return;
        }
        setOpen(null);
        await reload();
    };

    const visible = tasks.filter((t) => {
        if (filters.mine && t.created_by !== me) return false;
        if (filters.priority !== "all" && t.priority !== filters.priority) return false;
        if (filters.owner === "all") return true;
        if (filters.owner === "team") return !t.assignee_workflow_id && !t.assignee_user_id;
        if (filters.owner.startsWith("bot:")) return t.assignee_workflow_id === Number(filters.owner.slice(4));
        if (filters.owner.startsWith("user:")) return t.assignee_user_id === Number(filters.owner.slice(5));
        return true;
    });
    const inColumn = (id: string) => visible.filter((t) => t.status === id);
    const byId = new Map(tasks.map((t) => [t.id, t]));
    const opened = open === null ? null : (byId.get(open) ?? null);

    const onDrop = (status: string) => (event: React.DragEvent) => {
        event.preventDefault();
        const id = Number(event.dataTransfer.getData("text/plain") || dragging);
        setOver(null);
        setDragging(null);
        const task = byId.get(id);
        if (task) void move(task, status);
    };

    return (
        <>
            <PageHeader
                title="Tasks"
                description="One board people and agents both work from. An agent's finished task waits in review for you; a blocked one says why."
                tabs={tabs}
                actions={
                    <div className="flex items-center gap-1 rounded-md border border-border p-0.5 text-xs">
                        <button type="button" className={cn("rounded px-2 py-1", view === "board" && "bg-muted font-medium")} onClick={() => setView("board")} aria-pressed={view === "board"}>
                            Board
                        </button>
                        <button type="button" className={cn("rounded px-2 py-1", view === "list" && "bg-muted font-medium")} onClick={() => setView("list")} aria-pressed={view === "list"}>
                            List
                        </button>
                    </div>
                }
            />
            <div className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6">
                {error && (
                    <div className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div>
                )}

                <Card>
                    <CardContent className="grid gap-3 p-5 sm:grid-cols-[2fr_1fr_1fr_1fr]">
                        <div className="sm:col-span-4">
                            <Label htmlFor="task-title">New task</Label>
                            <Input id="task-title" className="mt-1" placeholder="Follow up Mrs Lakshmi about Tuesday" value={title} onChange={(e) => setTitle(e.target.value)} />
                        </div>
                        <div className="sm:col-span-4">
                            <Label htmlFor="task-brief">Brief</Label>
                            <Textarea id="task-brief" className="mt-1" rows={2} placeholder="Everything they need to do it without asking you." value={brief} onChange={(e) => setBrief(e.target.value)} />
                        </div>
                        <div>
                            <Label htmlFor="task-assignee">Owner</Label>
                            <select id="task-assignee" className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
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
                            <Label htmlFor="task-priority">Priority</Label>
                            <select id="task-priority" className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm" value={priority} onChange={(e) => setPriority(e.target.value)}>
                                {priorities.map((p) => (
                                    <option key={p} value={p}>{p}</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <Label htmlFor="task-due">Due</Label>
                            <Input id="task-due" className="mt-1" placeholder="in 3 days" value={due} onChange={(e) => setDue(e.target.value)} />
                        </div>
                        <div className="flex items-end gap-3">
                            <label className="flex items-center gap-2 text-sm">
                                <input type="checkbox" checked={backlog} onChange={(e) => setBacklog(e.target.checked)} />
                                Backlog
                            </label>
                            <Button disabled={filing || !title.trim()} onClick={() => void file()}>
                                {filing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                                File it
                            </Button>
                        </div>
                    </CardContent>
                </Card>

                <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
                    <label className="flex items-center gap-2">
                        <span className="text-muted-foreground">Owner</span>
                        <select aria-label="Filter by owner" className="h-8 rounded-md border border-input bg-background px-2 text-sm" value={filters.owner} onChange={(e) => setFilters({ ...filters, owner: e.target.value })}>
                            <option value="all">Everyone</option>
                            <option value="team">The team</option>
                            {people.map((p) => (
                                <option key={`fu${p.id}`} value={`user:${p.id}`}>{p.name}</option>
                            ))}
                            {bots.map((b) => (
                                <option key={`fb${b.id}`} value={`bot:${b.id}`}>{b.handle ? `@${b.handle}` : b.name}</option>
                            ))}
                        </select>
                    </label>
                    <label className="flex items-center gap-2">
                        <span className="text-muted-foreground">Priority</span>
                        <select aria-label="Filter by priority" className="h-8 rounded-md border border-input bg-background px-2 text-sm" value={filters.priority} onChange={(e) => setFilters({ ...filters, priority: e.target.value })}>
                            <option value="all">Any</option>
                            {priorities.map((p) => (
                                <option key={`fp${p}`} value={p}>{p}</option>
                            ))}
                        </select>
                    </label>
                    <label className="flex items-center gap-2">
                        <input type="checkbox" checked={filters.mine} onChange={(e) => setFilters({ ...filters, mine: e.target.checked })} />
                        Filed by me
                    </label>
                </div>

                {view === "board" ? (
                    <div className="mt-6 grid gap-3 overflow-x-auto md:grid-cols-4 xl:grid-cols-7">
                        {COLUMNS.map((column) => (
                            <section
                                key={column.id}
                                aria-label={column.label}
                                data-testid={`column-${column.id}`}
                                className={cn("min-w-0 rounded-lg border border-transparent p-1", over === column.id && "border-primary/50 bg-muted/40")}
                                onDragOver={(e) => {
                                    e.preventDefault();
                                    if (over !== column.id) setOver(column.id);
                                }}
                                onDragLeave={() => setOver((o) => (o === column.id ? null : o))}
                                onDrop={onDrop(column.id)}
                            >
                                <h2 className="text-sm font-semibold">
                                    {column.label}
                                    <span className="ml-2 text-xs font-normal text-muted-foreground">{inColumn(column.id).length}</span>
                                </h2>
                                <p className="text-xs text-muted-foreground">{column.hint}</p>
                                <ul className="mt-3 space-y-2">
                                    {inColumn(column.id).map((task) => (
                                        <TaskCard key={task.id} task={task} busy={busy === task.id} onOpen={() => setOpen(task.id)} onDragStart={(e) => { e.dataTransfer.setData("text/plain", String(task.id)); setDragging(task.id); }} />
                                    ))}
                                    {inColumn(column.id).length === 0 && (
                                        <li className="rounded-lg border border-dashed border-border p-3 text-xs text-muted-foreground">Nothing here.</li>
                                    )}
                                </ul>
                            </section>
                        ))}
                    </div>
                ) : (
                    <table className="mt-6 w-full text-sm">
                        <thead>
                            <tr className="border-b text-left text-xs text-muted-foreground">
                                <th className="py-2 pr-3">ID</th>
                                <th className="py-2 pr-3">Title</th>
                                <th className="py-2 pr-3">Status</th>
                                <th className="py-2 pr-3">Priority</th>
                                <th className="py-2 pr-3">Owner</th>
                                <th className="py-2 pr-3">Due</th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.map((task) => (
                                <tr key={task.id} className="cursor-pointer border-b hover:bg-muted/40" onClick={() => setOpen(task.id)}>
                                    <td className="py-2 pr-3 font-mono text-xs text-muted-foreground">{task.identifier}</td>
                                    <td className="py-2 pr-3">{task.title}</td>
                                    <td className="py-2 pr-3">{COLUMNS.find((c) => c.id === task.status)?.label ?? task.status}</td>
                                    <td className="py-2 pr-3">{task.priority}</td>
                                    <td className="py-2 pr-3">{ownerOf(task).name}</td>
                                    <td className="py-2 pr-3">{when(task.due_at)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {opened && (
                <TaskDialog
                    task={opened}
                    tasks={tasks}
                    bots={bots}
                    people={people}
                    priorities={priorities}
                    busy={busy === opened.id}
                    onClose={() => setOpen(null)}
                    onMove={(status) => void move(opened, status)}
                    onRemove={() => void remove(opened)}
                    onChanged={reload}
                    onError={setError}
                />
            )}
        </>
    );
}

function TaskCard({ task, busy, onOpen, onDragStart }: { task: Task; busy: boolean; onOpen: () => void; onDragStart: (e: React.DragEvent) => void }) {
    const owner = ownerOf(task);
    const Icon = owner.icon;
    return (
        <li
            draggable
            onDragStart={onDragStart}
            data-testid={`task-${task.id}`}
            className={cn("cursor-grab rounded-lg border border-border bg-card p-3 text-sm shadow-sm active:cursor-grabbing", busy && "opacity-60")}
        >
            <div className="flex items-start gap-2">
                <GripVertical className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <button type="button" className="min-w-0 flex-1 text-left" onClick={onOpen}>
                    <p className="flex items-center gap-2 text-[11px] text-muted-foreground">
                        <span className="font-mono">{task.identifier}</span>
                        <Badge variant="outline" className={cn("px-1.5 py-0 text-[10px]", PRIORITY_CLASS[task.priority] ?? "")}>{task.priority}</Badge>
                    </p>
                    <p className="mt-1 font-medium leading-snug">{task.title}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                        <span className="inline-flex items-center gap-1"><Icon className="h-3 w-3" aria-hidden />{owner.name}</span>
                        {task.due_at && <span>· due {when(task.due_at)}</span>}
                        {task.blocked_by.length > 0 && <span>· waits on {task.blocked_by.length}</span>}
                        {(task.comment_count ?? 0) > 0 && (
                            <span className="inline-flex items-center gap-1"><MessageSquare className="h-3 w-3" aria-hidden />{task.comment_count}</span>
                        )}
                        {task.status === "in_progress" && task.assignee_workflow_id && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
                    </p>
                    {task.result && (task.status === "in_review" || task.status === "blocked") && (
                        <p className={cn("mt-2 line-clamp-3 whitespace-pre-wrap border-l-2 pl-2 text-xs", task.status === "in_review" ? "border-emerald-400" : "border-amber-400")}>{task.result}</p>
                    )}
                </button>
            </div>
        </li>
    );
}

function TaskDialog({
    task,
    tasks,
    bots,
    people,
    priorities,
    busy,
    onClose,
    onMove,
    onRemove,
    onChanged,
    onError,
}: {
    task: Task;
    tasks: Task[];
    bots: { id: number; name: string; handle: string | null }[];
    people: { id: number; name: string }[];
    priorities: string[];
    busy: boolean;
    onClose: () => void;
    onMove: (status: string) => void;
    onRemove: () => void;
    onChanged: () => Promise<void>;
    onError: (message: string | null) => void;
}) {
    const [comments, setComments] = useState<Comment[] | null>(null);
    const [line, setLine] = useState("");
    const [saving, setSaving] = useState(false);
    const currentOwner = task.assignee_user_id
        ? `user:${task.assignee_user_id}`
        : task.assignee_workflow_id
            ? (() => {
                const bot = bots.find((b) => b.id === task.assignee_workflow_id);
                return bot ? (bot.handle ? `@${bot.handle}` : bot.name) : "team";
            })()
            : "team";
    const [blockedBy, setBlockedBy] = useState(task.blocked_by.join(", "));

    const loadComments = useCallback(async () => {
        const result = await getTaskApiV1TasksTaskIdGet({ path: { task_id: task.id } });
        if (result.error) {
            onError(detailFromResult(result, "Could not read the card"));
            return;
        }
        setComments(((result.data as { comments?: Comment[] } | undefined)?.comments) ?? []);
    }, [task.id, onError]);

    useEffect(() => {
        void loadComments();
    }, [loadComments]);

    const edit = async (changes: Record<string, unknown>) => {
        setSaving(true);
        onError(null);
        const result = await editTaskApiV1TasksTaskIdPatch({ path: { task_id: task.id }, body: changes });
        setSaving(false);
        if (result.error) {
            onError(detailFromResult(result, "Could not change the card"));
            return;
        }
        await onChanged();
    };

    const say = async () => {
        if (!line.trim()) return;
        setSaving(true);
        const result = await addCommentApiV1TasksTaskIdCommentsPost({ path: { task_id: task.id }, body: { body: line } });
        setSaving(false);
        if (result.error) {
            onError(detailFromResult(result, "Could not add the line"));
            return;
        }
        setLine("");
        await loadComments();
        await onChanged();
    };

    const parent = task.parent_id ? tasks.find((t) => t.id === task.parent_id) : null;

    return (
        <Dialog open onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <span className="font-mono text-xs text-muted-foreground">{task.identifier}</span>
                        {task.title}
                    </DialogTitle>
                    <DialogDescription>
                        {task.from_name ? `Filed by ${task.from_name}` : "Filed by a person"}
                        {task.created_at && ` · ${when(task.created_at)}`}
                        {parent && ` · under ${parent.identifier ?? parent.title}`}
                    </DialogDescription>
                </DialogHeader>

                <div className="grid gap-3 sm:grid-cols-3">
                    <div>
                        <Label htmlFor="card-status">Column</Label>
                        <select id="card-status" className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm" value={task.status} disabled={busy} onChange={(e) => onMove(e.target.value)}>
                            {COLUMNS.map((c) => (
                                <option key={c.id} value={c.id}>{c.label}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <Label htmlFor="card-priority">Priority</Label>
                        <select id="card-priority" className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm" value={task.priority} disabled={saving} onChange={(e) => void edit({ priority: e.target.value })}>
                            {priorities.map((p) => (
                                <option key={p} value={p}>{p}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <Label htmlFor="card-owner">Owner</Label>
                        <select id="card-owner" className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm" value={currentOwner} disabled={saving} onChange={(e) => void edit({ assignee: e.target.value })}>
                            <option value="team">The team</option>
                            {people.map((p) => (
                                <option key={`du${p.id}`} value={`user:${p.id}`}>{p.name}</option>
                            ))}
                            {bots.map((b) => (
                                <option key={`db${b.id}`} value={b.handle ? `@${b.handle}` : b.name}>{b.handle ? `@${b.handle}` : b.name}</option>
                            ))}
                        </select>
                    </div>
                    <div className="sm:col-span-2">
                        <Label htmlFor="card-blocked">Waits on (task numbers, comma separated)</Label>
                        <Input
                            id="card-blocked"
                            className="mt-1"
                            value={blockedBy}
                            onChange={(e) => setBlockedBy(e.target.value)}
                            onBlur={() => {
                                const ids = blockedBy.split(/[\s,]+/).map((s) => Number(s.replace(/^\D+-?/, ""))).filter((n) => Number.isFinite(n) && n > 0);
                                const asIds = ids.map((n) => tasks.find((t) => t.number === n)?.id ?? n);
                                void edit({ blocked_by: asIds });
                            }}
                        />
                    </div>
                    <div>
                        <Label htmlFor="card-due">Due</Label>
                        <Input id="card-due" className="mt-1" placeholder="in 3 days" defaultValue={task.due_at ? task.due_at.slice(0, 10) : ""} onBlur={(e) => void edit({ due: e.target.value || null })} />
                    </div>
                </div>

                {task.brief && (
                    <div>
                        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Brief</p>
                        <p className="mt-1 whitespace-pre-wrap text-sm">{task.brief}</p>
                    </div>
                )}
                {task.result && (
                    <div>
                        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Latest report</p>
                        <p className={cn("mt-1 whitespace-pre-wrap border-l-2 pl-2 text-sm", task.status === "blocked" ? "border-amber-400" : "border-emerald-400")}>{task.result}</p>
                    </div>
                )}

                <div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">On the card</p>
                    <ul className="mt-2 space-y-2">
                        {comments === null && <li className="text-xs text-muted-foreground">Reading…</li>}
                        {comments?.length === 0 && <li className="text-xs text-muted-foreground">Nothing yet.</li>}
                        {comments?.map((c) => (
                            <li key={c.id} className="rounded-md border border-border p-2 text-sm">
                                <p className="text-xs text-muted-foreground">
                                    {c.author_name ?? (c.author_workflow_id ? "An agent" : "A person")}
                                    {c.created_at && ` · ${when(c.created_at)}`}
                                </p>
                                <p className="mt-1 whitespace-pre-wrap">{c.body}</p>
                            </li>
                        ))}
                    </ul>
                    <div className="mt-2 flex gap-2">
                        <Input aria-label="Add a line" placeholder="Say something on the card" value={line} onChange={(e) => setLine(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void say()} />
                        <Button variant="outline" disabled={saving || !line.trim()} onClick={() => void say()}>Add</Button>
                    </div>
                </div>

                <div className="flex items-center justify-between pt-2">
                    {task.status === "in_review" ? (
                        <div className="flex gap-2">
                            <Button size="sm" disabled={busy} onClick={() => onMove("done")}>Sign off</Button>
                            <Button size="sm" variant="outline" disabled={busy} onClick={() => onMove("todo")}>Send back</Button>
                        </div>
                    ) : task.status === "blocked" && task.assignee_workflow_id ? (
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => onMove("todo")}>Run again</Button>
                    ) : (
                        <span />
                    )}
                    <Button size="sm" variant="ghost" className="text-muted-foreground" aria-label={`Remove ${task.title}`} disabled={busy} onClick={onRemove}>
                        <Trash2 className="mr-1 h-3.5 w-3.5" aria-hidden /> Remove
                    </Button>
                </div>
            </DialogContent>
        </Dialog>
    );
}
