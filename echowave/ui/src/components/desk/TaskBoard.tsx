/**
 * The board, on paperclip's issue model (TB-1, TB-2).
 *
 * An inbox down the side -- Mine, Needs me, All tasks -- and the same tasks
 * either as a list grouped by status, priority or owner, or as seven
 * columns to drag cards between. A task opens on its own page (thread,
 * activity, sub-tasks, properties); nothing is edited in a pop-up. A bot at
 * work on a task shows a live dot, and the board polls faster while one is.
 * A new task is one button away, not a form that sits over the board.
 */

"use client";

import { GripVertical, Loader2, MessageSquare, Plus, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { createTaskApiV1TasksPost, listTasksApiV1TasksGet, setTaskStatusApiV1TasksTaskIdStatusPost } from "@/client/sdk.gen";
import { PageHeader, type PageTab } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { detailFromResult } from "@/lib/apiError";
import { cn } from "@/lib/utils";

import { LabelChip, LabelPicker, SubtaskProgress } from "./TaskInputs";
import {
    type BoardPayload,
    type BotRef,
    COLUMNS,
    type GroupBy,
    groupTasks,
    hasLabel,
    type Inbox,
    INBOXES,
    inInbox,
    isLive,
    matches,
    ownerOf,
    type Person,
    PRIORITY_CLASS,
    statusLabel,
    subtaskProgress,
    type Task,
    when,
} from "./tasks";

export { type BoardPayload, COLUMNS, type Comment, type Task } from "./tasks";

const SELECT = "h-8 rounded-md border border-input bg-background px-2 text-sm";

/** The circle at the start of a row: paperclip's status mark. */
export function StatusDot({ status }: { status: string }) {
    const tone: Record<string, string> = {
        backlog: "border-dashed border-muted-foreground",
        todo: "border-muted-foreground",
        in_progress: "border-amber-500 bg-amber-500/30",
        in_review: "border-violet-500 bg-violet-500/30",
        done: "border-emerald-600 bg-emerald-600",
        blocked: "border-red-500 bg-red-500/30",
        cancelled: "border-muted-foreground bg-muted-foreground/40",
    };
    return <span aria-label={statusLabel(status)} className={cn("inline-block h-3 w-3 shrink-0 rounded-full border-2", tone[status] ?? "border-muted-foreground")} />;
}

export function LiveDot() {
    return (
        <span className="relative inline-flex h-2 w-2" aria-label="An agent is working on it" title="An agent is working on it">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
        </span>
    );
}

type Props = { initial: BoardPayload; tabs: PageTab[] };

export function TaskBoard({ initial, tabs }: Props) {
    const router = useRouter();
    const [payload, setPayload] = useState<BoardPayload>(initial);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<number | null>(null);
    const [view, setView] = useState<"list" | "board">("list");
    const [inbox, setInbox] = useState<Inbox>("all");
    const [groupBy, setGroupBy] = useState<GroupBy>("status");
    const [query, setQuery] = useState("");
    const [owner, setOwner] = useState("all");
    const [priority, setPriority] = useState("all");
    const [label, setLabel] = useState("all");
    const [creating, setCreating] = useState(false);
    const [dragging, setDragging] = useState<number | null>(null);
    const [over, setOver] = useState<string | null>(null);

    const tasks = useMemo(() => payload.tasks ?? [], [payload]);
    const bots = payload.bots ?? [];
    const people = payload.board?.people ?? [];
    const priorities = payload.board?.priorities ?? ["critical", "high", "medium", "low"];
    const me = payload.board?.me ?? null;
    const labels = payload.board?.labels ?? [];
    const anyLive = tasks.some(isLive);

    const reload = useCallback(async () => {
        const result = await listTasksApiV1TasksGet();
        if (result.error) {
            setError(detailFromResult(result, "Could not load the board"));
            return;
        }
        setPayload((result.data as BoardPayload | undefined) ?? {});
    }, []);

    useEffect(() => {
        // Nothing tells this screen a bot finished; a poll does, and a
        // quicker one while a bot is at work so its dot goes out on time.
        const timer = setInterval(() => void reload(), anyLive ? 5_000 : 15_000);
        return () => clearInterval(timer);
    }, [reload, anyLive]);

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

    const counts = Object.fromEntries(INBOXES.map((i) => [i.id, tasks.filter((t) => inInbox(t, i.id, me)).length])) as Record<Inbox, number>;
    const visible = tasks.filter((t) => {
        if (!inInbox(t, inbox, me) || !matches(t, query)) return false;
        if (priority !== "all" && t.priority !== priority) return false;
        if (label !== "all" && !hasLabel(t, label)) return false;
        if (owner === "all") return true;
        if (owner === "team") return !t.assignee_workflow_id && !t.assignee_user_id;
        if (owner.startsWith("bot:")) return t.assignee_workflow_id === Number(owner.slice(4));
        if (owner.startsWith("user:")) return t.assignee_user_id === Number(owner.slice(5));
        return true;
    });
    const byId = new Map(tasks.map((t) => [t.id, t]));
    const open = (task: Task) => router.push(`/tasks/${task.id}`);

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
                    <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1 rounded-md border border-border p-0.5 text-xs">
                            {(["list", "board"] as const).map((v) => (
                                <button key={v} type="button" className={cn("rounded px-2 py-1 capitalize", view === v && "bg-muted font-medium")} onClick={() => setView(v)} aria-pressed={view === v}>
                                    {v === "list" ? "List" : "Board"}
                                </button>
                            ))}
                        </div>
                        <Button size="sm" onClick={() => setCreating(true)}>
                            <Plus className="mr-1 h-4 w-4" aria-hidden /> New task
                        </Button>
                    </div>
                }
            />
            <div className="mx-auto grid w-full max-w-[1600px] gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[200px_1fr]">
                <nav aria-label="Inbox" className="flex gap-1 overflow-x-auto lg:flex-col">
                    {INBOXES.map((i) => (
                        <button
                            key={i.id}
                            type="button"
                            title={i.hint}
                            aria-current={inbox === i.id ? "page" : undefined}
                            onClick={() => setInbox(i.id)}
                            className={cn("flex items-center justify-between gap-3 whitespace-nowrap rounded-md px-3 py-1.5 text-left text-sm hover:bg-muted", inbox === i.id && "bg-muted font-medium")}
                        >
                            {i.label}
                            <span className="text-xs text-muted-foreground">{counts[i.id]}</span>
                        </button>
                    ))}
                </nav>

                <div className="min-w-0">
                    {error && <div className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div>}

                    <div className="flex flex-wrap items-center gap-2 text-sm">
                        <div className="relative">
                            <Search className="absolute left-2 top-2 h-4 w-4 text-muted-foreground" aria-hidden />
                            <Input aria-label="Search tasks" placeholder="Search" className="h-8 w-48 pl-7" value={query} onChange={(e) => setQuery(e.target.value)} />
                        </div>
                        <select aria-label="Filter by owner" className={SELECT} value={owner} onChange={(e) => setOwner(e.target.value)}>
                            <option value="all">Any owner</option>
                            <option value="team">The team</option>
                            {people.map((p) => (
                                <option key={`fu${p.id}`} value={`user:${p.id}`}>{p.name}</option>
                            ))}
                            {bots.map((b) => (
                                <option key={`fb${b.id}`} value={`bot:${b.id}`}>{b.handle ? `@${b.handle}` : b.name}</option>
                            ))}
                        </select>
                        <select aria-label="Filter by priority" className={SELECT} value={priority} onChange={(e) => setPriority(e.target.value)}>
                            <option value="all">Any priority</option>
                            {priorities.map((p) => (
                                <option key={`fp${p}`} value={p}>{p}</option>
                            ))}
                        </select>
                        {labels.length > 0 && (
                            <select aria-label="Filter by label" className={SELECT} value={label} onChange={(e) => setLabel(e.target.value)}>
                                <option value="all">Any label</option>
                                {labels.map((l) => (
                                    <option key={`fl${l}`} value={l}>{l}</option>
                                ))}
                            </select>
                        )}
                        {view === "list" && (
                            <select aria-label="Group by" className={SELECT} value={groupBy} onChange={(e) => setGroupBy(e.target.value as GroupBy)}>
                                <option value="status">Group: status</option>
                                <option value="priority">Group: priority</option>
                                <option value="owner">Group: owner</option>
                                <option value="none">No grouping</option>
                            </select>
                        )}
                    </div>

                    {view === "list" ? (
                        <TaskList tasks={visible} all={tasks} groupBy={groupBy} onOpen={open} />
                    ) : (
                        <div className="mt-4 grid gap-3 overflow-x-auto md:grid-cols-4 xl:grid-cols-7">
                            {COLUMNS.map((column) => {
                                const cards = visible.filter((t) => t.status === column.id);
                                return (
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
                                        <h2 className="flex items-center gap-2 text-sm font-semibold">
                                            <StatusDot status={column.id} />
                                            {column.label}
                                            <span className="text-xs font-normal text-muted-foreground">{cards.length}</span>
                                        </h2>
                                        <p className="text-xs text-muted-foreground">{column.hint}</p>
                                        <ul className="mt-3 space-y-2">
                                            {cards.map((task) => (
                                                <TaskCard
                                                    key={task.id}
                                                    task={task}
                                                    progress={subtaskProgress(task, tasks)}
                                                    busy={busy === task.id}
                                                    onOpen={() => open(task)}
                                                    onDragStart={(e) => {
                                                        e.dataTransfer.setData("text/plain", String(task.id));
                                                        setDragging(task.id);
                                                    }}
                                                />
                                            ))}
                                            {cards.length === 0 && <li className="rounded-lg border border-dashed border-border p-3 text-xs text-muted-foreground">Nothing here.</li>}
                                        </ul>
                                    </section>
                                );
                            })}
                        </div>
                    )}
                </div>
            </div>

            {creating && (
                <NewTaskDialog
                    bots={bots}
                    people={people}
                    priorities={priorities}
                    knownLabels={labels}
                    onClose={() => setCreating(false)}
                    onFiled={async () => {
                        setCreating(false);
                        await reload();
                    }}
                />
            )}
        </>
    );
}

function TaskList({ tasks, all, groupBy, onOpen }: { tasks: Task[]; all: Task[]; groupBy: GroupBy; onOpen: (t: Task) => void }) {
    if (tasks.length === 0) return <p className="mt-6 text-sm text-muted-foreground">Nothing here.</p>;
    return (
        <div className="mt-4 space-y-5" role="table" aria-label="Tasks">
            {groupTasks(tasks, groupBy).map((group) => (
                <section key={group.key} aria-label={group.label}>
                    {groupBy !== "none" && (
                        <h2 className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                            {groupBy === "status" && <StatusDot status={group.key} />}
                            {group.label}
                            <span className="font-normal">{group.tasks.length}</span>
                        </h2>
                    )}
                    <ul className="divide-y rounded-md border border-border">
                        {group.tasks.map((task) => {
                            const who = ownerOf(task);
                            const Icon = who.icon;
                            const progress = subtaskProgress(task, all);
                            return (
                                <li key={task.id} role="row">
                                    <button type="button" onClick={() => onOpen(task)} className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-muted/50">
                                        <StatusDot status={task.status} />
                                        <span className="w-16 shrink-0 font-mono text-xs text-muted-foreground">{task.identifier}</span>
                                        <span className="min-w-0 flex-1 truncate">{task.title}</span>
                                        <span className="hidden gap-1 sm:inline-flex">
                                            {(task.labels ?? []).slice(0, 3).map((l) => (
                                                <LabelChip key={l} label={l} />
                                            ))}
                                        </span>
                                        {isLive(task) && <LiveDot />}
                                        {progress && <SubtaskProgress {...progress} />}
                                        {(task.comment_count ?? 0) > 0 && (
                                            <span className="hidden items-center gap-1 text-xs text-muted-foreground sm:inline-flex">
                                                <MessageSquare className="h-3 w-3" aria-hidden />
                                                {task.comment_count}
                                            </span>
                                        )}
                                        <Badge variant="outline" className={cn("hidden px-1.5 py-0 text-[10px] sm:inline-flex", PRIORITY_CLASS[task.priority] ?? "")}>{task.priority}</Badge>
                                        <span className="hidden w-32 items-center gap-1 truncate text-xs text-muted-foreground md:inline-flex">
                                            <Icon className="h-3 w-3 shrink-0" aria-hidden />
                                            {who.name}
                                        </span>
                                        <span className="hidden w-24 text-right text-xs text-muted-foreground lg:inline">{when(task.due_at ?? task.created_at)}</span>
                                    </button>
                                </li>
                            );
                        })}
                    </ul>
                </section>
            ))}
        </div>
    );
}

function TaskCard({
    task,
    progress,
    busy,
    onOpen,
    onDragStart,
}: {
    task: Task;
    progress: { done: number; total: number } | null;
    busy: boolean;
    onOpen: () => void;
    onDragStart: (e: React.DragEvent) => void;
}) {
    const owner = ownerOf(task);
    const Icon = owner.icon;
    return (
        <li draggable onDragStart={onDragStart} data-testid={`task-${task.id}`} className={cn("cursor-grab rounded-lg border border-border bg-card p-3 text-sm shadow-sm active:cursor-grabbing", busy && "opacity-60")}>
            <div className="flex items-start gap-2">
                <GripVertical className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <button type="button" className="min-w-0 flex-1 text-left" onClick={onOpen}>
                    <p className="flex items-center gap-2 text-[11px] text-muted-foreground">
                        <span className="font-mono">{task.identifier}</span>
                        <Badge variant="outline" className={cn("px-1.5 py-0 text-[10px]", PRIORITY_CLASS[task.priority] ?? "")}>{task.priority}</Badge>
                        {isLive(task) && <LiveDot />}
                    </p>
                    <p className="mt-1 font-medium leading-snug">{task.title}</p>
                    {(task.labels ?? []).length > 0 && (
                        <p className="mt-1 flex flex-wrap gap-1">
                            {task.labels!.map((l) => (
                                <LabelChip key={l} label={l} />
                            ))}
                        </p>
                    )}
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                            <Icon className="h-3 w-3" aria-hidden />
                            {owner.name}
                        </span>
                        {task.due_at && <span>· due {when(task.due_at)}</span>}
                        {task.blocked_by.length > 0 && <span>· waits on {task.blocked_by.length}</span>}
                        {progress && <SubtaskProgress {...progress} />}
                        {(task.comment_count ?? 0) > 0 && (
                            <span className="inline-flex items-center gap-1">
                                <MessageSquare className="h-3 w-3" aria-hidden />
                                {task.comment_count}
                            </span>
                        )}
                    </p>
                    {task.result && (task.status === "in_review" || task.status === "blocked") && (
                        <p className={cn("mt-2 line-clamp-3 whitespace-pre-wrap border-l-2 pl-2 text-xs", task.status === "in_review" ? "border-emerald-400" : "border-amber-400")}>{task.result}</p>
                    )}
                </button>
            </div>
        </li>
    );
}

/** A new task, or a sub-task when ``parentId`` is given. */
export function NewTaskDialog({
    bots,
    people,
    priorities,
    knownLabels = [],
    parentId,
    onClose,
    onFiled,
}: {
    bots: BotRef[];
    people: Person[];
    priorities: string[];
    knownLabels?: string[];
    parentId?: number;
    onClose: () => void;
    onFiled: () => Promise<void> | void;
}) {
    const [title, setTitle] = useState("");
    const [brief, setBrief] = useState("");
    const [assignee, setAssignee] = useState("team");
    const [priority, setPriority] = useState("medium");
    const [due, setDue] = useState("");
    const [backlog, setBacklog] = useState(false);
    const [taskLabels, setTaskLabels] = useState<string[]>([]);
    const [filing, setFiling] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const file = async () => {
        if (!title.trim()) return;
        setFiling(true);
        setError(null);
        const result = await createTaskApiV1TasksPost({
            body: {
                title,
                brief,
                assignee,
                priority,
                due: due || null,
                backlog,
                ...(taskLabels.length ? { labels: taskLabels } : {}),
                ...(parentId ? { parent_id: parentId } : {}),
            },
        });
        setFiling(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not file the task"));
            return;
        }
        await onFiled();
    };

    return (
        <Dialog open onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="sm:max-w-lg">
                <DialogHeader>
                    <DialogTitle>{parentId ? "New sub-task" : "New task"}</DialogTitle>
                    <DialogDescription>Give it to an agent by @handle and it starts now; give it to the team or a person and it waits for them.</DialogDescription>
                </DialogHeader>
                <div className="grid gap-3 sm:grid-cols-3">
                    <div className="sm:col-span-3">
                        <Label htmlFor="task-title">Title</Label>
                        <Input id="task-title" className="mt-1" autoFocus placeholder="Follow up Mrs Lakshmi about Tuesday" value={title} onChange={(e) => setTitle(e.target.value)} />
                    </div>
                    <div className="sm:col-span-3">
                        <Label htmlFor="task-brief">Brief</Label>
                        <Textarea id="task-brief" className="mt-1" rows={3} placeholder="Everything they need to do it without asking you." value={brief} onChange={(e) => setBrief(e.target.value)} />
                    </div>
                    <div>
                        <Label htmlFor="task-assignee">Owner</Label>
                        <select id="task-assignee" className={cn(SELECT, "mt-1 h-9 w-full")} value={assignee} onChange={(e) => setAssignee(e.target.value)}>
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
                        <select id="task-priority" className={cn(SELECT, "mt-1 h-9 w-full")} value={priority} onChange={(e) => setPriority(e.target.value)}>
                            {priorities.map((p) => (
                                <option key={p} value={p}>{p}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <Label htmlFor="task-due">Due</Label>
                        <Input id="task-due" className="mt-1" placeholder="in 3 days" value={due} onChange={(e) => setDue(e.target.value)} />
                    </div>
                    <div className="sm:col-span-3">
                        <Label htmlFor="task-labels">Labels</Label>
                        <LabelPicker id="task-labels" value={taskLabels} known={knownLabels} onChange={setTaskLabels} />
                    </div>
                </div>
                {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
                <DialogFooter className="items-center gap-3 sm:justify-between">
                    <label className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={backlog} onChange={(e) => setBacklog(e.target.checked)} />
                        Backlog
                    </label>
                    <Button disabled={filing || !title.trim()} onClick={() => void file()}>
                        {filing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                        File it
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
