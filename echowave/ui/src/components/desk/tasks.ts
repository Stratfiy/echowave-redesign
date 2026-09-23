/**
 * What the board, its list and a task's own page share (TB-2): the task's
 * shape, the seven columns, and the rules that sort a board into an inbox.
 * Pure functions, so the inbox and the grouping are tested without a screen.
 */

import { Bot, User, Users } from "lucide-react";

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
    started_at?: string | null;
    finished_at?: string | null;
};

export type Comment = {
    id: number;
    body: string;
    author_name: string | null;
    author_workflow_id: number | null;
    author_user_id: number | null;
    created_at: string | null;
};

export type BotRef = { id: number; name: string; handle: string | null };
export type Person = { id: number; name: string };

export type BoardPayload = {
    tasks?: Task[];
    statuses?: string[];
    bots?: BotRef[];
    board?: {
        enabled?: boolean;
        priorities?: string[];
        prefix?: string;
        people?: Person[];
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

export const statusLabel = (status: string) => COLUMNS.find((c) => c.id === status)?.label ?? status;

export const PRIORITIES = ["critical", "high", "medium", "low"];

export const PRIORITY_CLASS: Record<string, string> = {
    critical: "border-transparent bg-red-100 text-red-900",
    high: "border-transparent bg-amber-100 text-amber-900",
    medium: "border-border/70 bg-background/80 text-muted-foreground",
    low: "border-transparent bg-slate-100 text-slate-700",
};

export function when(iso: string | null | undefined): string {
    if (!iso) return "";
    return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function ownerOf(task: Task): { icon: typeof Bot; name: string } {
    if (task.assignee_name) return { icon: Bot, name: task.assignee_name };
    if (task.assignee_user_name) return { icon: User, name: task.assignee_user_name };
    return { icon: Users, name: "The team" };
}

/** A bot is working it right now: the live dot. */
export const isLive = (task: Task) => task.status === "in_progress" && task.assignee_workflow_id !== null;

/** The value an owner picker holds for a task's current owner. */
export function ownerValue(task: Task, bots: BotRef[]): string {
    if (task.assignee_user_id) return `user:${task.assignee_user_id}`;
    if (!task.assignee_workflow_id) return "team";
    const bot = bots.find((b) => b.id === task.assignee_workflow_id);
    return bot ? (bot.handle ? `@${bot.handle}` : bot.name) : "team";
}

// --- the inbox -------------------------------------------------------------

export type Inbox = "mine" | "needs_me" | "all";

export const INBOXES: { id: Inbox; label: string; hint: string }[] = [
    { id: "mine", label: "Mine", hint: "Yours to do, or filed by you" },
    { id: "needs_me", label: "Needs me", hint: "Reports to sign off, and blocked work" },
    { id: "all", label: "All tasks", hint: "Everything on the board" },
];

const OPEN = (t: Task) => t.status !== "done" && t.status !== "cancelled";

/** Mine is what is yours and still open; Needs me is the review queue and
 *  what is stuck, whoever it belongs to -- a bot cannot unblock itself. */
export function inInbox(task: Task, inbox: Inbox, me: number | null): boolean {
    if (inbox === "all") return true;
    if (inbox === "needs_me") return task.status === "in_review" || task.status === "blocked";
    return OPEN(task) && me !== null && (task.assignee_user_id === me || task.created_by === me);
}

// --- grouping the list -----------------------------------------------------

export type GroupBy = "status" | "priority" | "owner" | "none";

export function groupTasks(tasks: Task[], by: GroupBy): { key: string; label: string; tasks: Task[] }[] {
    if (by === "none") return [{ key: "all", label: "All", tasks }];
    const order =
        by === "status"
            ? COLUMNS.map((c) => ({ key: c.id, label: c.label }))
            : by === "priority"
              ? PRIORITIES.map((p) => ({ key: p, label: p[0].toUpperCase() + p.slice(1) }))
              : [];
    const keyOf = (t: Task) => (by === "status" ? t.status : by === "priority" ? t.priority : ownerOf(t).name);
    const groups = new Map<string, Task[]>();
    for (const t of tasks) groups.set(keyOf(t), [...(groups.get(keyOf(t)) ?? []), t]);
    const known = order.filter((o) => groups.has(o.key)).map((o) => ({ ...o, tasks: groups.get(o.key)! }));
    // Anything the order does not name still shows, after the named ones.
    const rest = [...groups.keys()]
        .filter((k) => !order.some((o) => o.key === k))
        .sort()
        .map((k) => ({ key: k, label: k, tasks: groups.get(k)! }));
    return [...known, ...rest];
}

export function matches(task: Task, query: string): boolean {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return [task.identifier, task.title, task.brief, task.assignee_name, task.assignee_user_name]
        .filter(Boolean)
        .some((s) => String(s).toLowerCase().includes(q));
}

/** The ids a "waits on" box names: DEC-12, 12 or #12, as task ids. */
export function parseWaitsOn(text: string, tasks: Task[]): number[] {
    return text
        .split(/[\s,]+/)
        .map((s) => Number(s.replace(/^\D+-?/, "")))
        .filter((n) => Number.isFinite(n) && n > 0)
        .map((n) => tasks.find((t) => t.number === n)?.id ?? n);
}
