/**
 * The Company page's arithmetic (Paperclip-style), kept apart from the screen
 * so it is tested without one.
 *
 * Everything here is read from endpoints that already exist: /team/status for
 * what each agent is doing, /workflow/fetch and /folder for which team it sits
 * in, /tasks for the work, /organizations/budgets for the money and
 * /routines for the heartbeats. There is no reports-to field on an agent yet,
 * so the chart is you, then your teams (folders), then their agents.
 */

import type {
    BudgetIncidentResponse,
    BudgetPolicyResponse,
    FolderResponse,
    RoutineResponse,
    TeamMember,
    WorkflowListResponse,
} from "@/client/types.gen";
import type { Avatar } from "@/components/avatar/avatar";
import type { Task } from "@/components/desk/tasks";

export type Budget = { spent: number; limit: number; percent: number; exhausted: boolean; warned: boolean };

export type OrgAgent = {
    id: number;
    name: string;
    handle: string | null;
    tone: string;
    isLive: boolean;
    status: string;
    lastLine: string | null;
    lastAt: string | null;
    calls: number;
    outcomes: number;
    failures: number;
    openTasks: number;
    budget: Budget | null;
    /** The agent's chosen face; null draws its starter face. */
    avatar: Avatar | null;
};

export type OrgTeam = { id: number | null; name: string; agents: OrgAgent[] };

export const NO_TEAM = "No team yet";

const OPEN_STATUSES = new Set(["backlog", "todo", "in_progress", "in_review", "blocked"]);
export const isOpen = (task: Pick<Task, "status">) => OPEN_STATUSES.has(task.status);

/** Attention first, then working, idle and paused: the order a manager reads. */
const TONE_RANK: Record<string, number> = { attention: 0, working: 1, idle: 2, paused: 3 };
const toneRank = (tone: string) => TONE_RANK[tone] ?? 4;

export function budgetOf(policy: BudgetPolicyResponse | undefined): Budget | null {
    if (!policy || policy.amount_credits <= 0) return null;
    return {
        spent: policy.spent_credits,
        limit: policy.amount_credits,
        percent: Math.min(100, Math.round((policy.spent_credits / policy.amount_credits) * 100)),
        exhausted: policy.exhausted,
        warned: policy.warned,
    };
}

/**
 * The chart: one team per folder, in the folders' own order, and the agents in
 * no folder last. A team with nobody in it is left out. An agent is anyone
 * /team/status lists (the active ones), placed by the workflow list's
 * folder_id; an agent the workflow list does not know lands in "No team yet".
 */
export function buildOrg(input: {
    members: TeamMember[];
    workflows: WorkflowListResponse[];
    folders: FolderResponse[];
    policies: BudgetPolicyResponse[];
    tasks: Task[];
}): OrgTeam[] {
    const workflowById = new Map(input.workflows.map((w) => [w.id, w]));
    const policyByWorkflow = new Map(
        input.policies.filter((p) => p.workflow_id !== null).map((p) => [p.workflow_id as number, p]),
    );
    const openByWorkflow = new Map<number, number>();
    for (const task of input.tasks) {
        if (task.assignee_workflow_id === null || !isOpen(task)) continue;
        openByWorkflow.set(task.assignee_workflow_id, (openByWorkflow.get(task.assignee_workflow_id) ?? 0) + 1);
    }

    const teams = new Map<number | null, OrgTeam>();
    for (const folder of input.folders) teams.set(folder.id, { id: folder.id, name: folder.name, agents: [] });
    teams.set(null, { id: null, name: NO_TEAM, agents: [] });

    for (const member of input.members) {
        const workflow = workflowById.get(member.workflow_id);
        const folderId = workflow?.folder_id ?? null;
        const team = teams.get(folderId) ?? teams.get(null)!;
        team.agents.push({
            id: member.workflow_id,
            name: member.name,
            handle: workflow?.handle ?? null,
            tone: member.tone,
            isLive: member.is_live,
            status: member.status,
            lastLine: member.last_line ?? null,
            lastAt: member.last_at ?? member.at ?? null,
            calls: member.calls,
            outcomes: member.outcomes,
            failures: member.failures,
            openTasks: openByWorkflow.get(member.workflow_id) ?? 0,
            budget: budgetOf(policyByWorkflow.get(member.workflow_id)),
            avatar: ((member.avatar ?? workflow?.avatar ?? null) as Avatar | null),
        });
    }

    for (const team of teams.values()) {
        team.agents.sort((a, b) => toneRank(a.tone) - toneRank(b.tone) || a.name.localeCompare(b.name));
    }
    return [...teams.values()].filter((team) => team.agents.length > 0);
}

export type Headline = {
    agents: number;
    working: number;
    needsYou: number;
    openTasks: number;
    inProgress: number;
    inReview: number;
    blocked: number;
    spent: number;
    limit: number | null;
};

/**
 * The strip across the top. Spend is the organisation-wide cap when there is
 * one (the policy with no workflow), otherwise the sum of the agents' caps,
 * and no limit at all when nobody set one.
 */
export function headline(members: TeamMember[], tasks: Task[], policies: BudgetPolicyResponse[]): Headline {
    const org = policies.find((p) => p.workflow_id === null);
    const perAgent = policies.filter((p) => p.workflow_id !== null);
    return {
        agents: members.length,
        working: members.filter((m) => m.tone === "working").length,
        needsYou: members.filter((m) => m.tone === "attention").length,
        openTasks: tasks.filter(isOpen).length,
        inProgress: tasks.filter((t) => t.status === "in_progress").length,
        inReview: tasks.filter((t) => t.status === "in_review").length,
        blocked: tasks.filter((t) => t.status === "blocked").length,
        spent: org ? org.spent_credits : perAgent.reduce((sum, p) => sum + p.spent_credits, 0),
        limit: org ? org.amount_credits : perAgent.length ? perAgent.reduce((sum, p) => sum + p.amount_credits, 0) : null,
    };
}

export type InboxItem = {
    key: string;
    kind: "budget" | "blocked" | "review" | "agent";
    title: string;
    detail: string;
    href: string;
    at: string | null;
    incidentId?: number;
};

const KIND_RANK: Record<InboxItem["kind"], number> = { budget: 0, blocked: 1, review: 2, agent: 3 };

/**
 * What is waiting on a person, Paperclip's governance queue built from what
 * we have: budget alerts (a hard stop before a warning), blocked work, reports
 * waiting for sign-off, and agents whose tone says they asked for help.
 */
export function inbox(input: {
    incidents: BudgetIncidentResponse[];
    tasks: Task[];
    members: TeamMember[];
    nameOf: (workflowId: number | null) => string;
}): InboxItem[] {
    const items: InboxItem[] = [];
    const incidents = input.incidents
        .filter((i) => i.status === "open")
        .sort((a, b) => (a.threshold === "hard" ? 0 : 1) - (b.threshold === "hard" ? 0 : 1));
    for (const incident of incidents) {
        const who = incident.workflow_id === null ? "The workspace" : input.nameOf(incident.workflow_id);
        const hard = incident.threshold === "hard";
        items.push({
            key: `budget-${incident.id}`,
            kind: "budget",
            title: hard ? `${who} hit its budget` : `${who} is near its budget`,
            detail: `${incident.observed_credits} of ${incident.limit_credits} credits${hard ? ", paused until you raise it" : ""}`,
            href: incident.workflow_id === null ? "/settings/company" : `/workflow/${incident.workflow_id}`,
            at: incident.created_at,
            incidentId: incident.id,
        });
    }
    for (const task of input.tasks) {
        if (task.status !== "blocked" && task.status !== "in_review") continue;
        const owner = task.assignee_name ?? task.assignee_user_name ?? "The team";
        items.push({
            key: `task-${task.id}`,
            kind: task.status === "blocked" ? "blocked" : "review",
            title: task.title,
            detail: task.status === "blocked" ? `${owner} is blocked` : `${owner} sent a report to sign off`,
            href: `/tasks/${task.id}`,
            at: task.finished_at ?? task.started_at ?? task.created_at,
        });
    }
    for (const member of input.members) {
        if (member.tone !== "attention") continue;
        items.push({
            key: `agent-${member.workflow_id}`,
            kind: "agent",
            title: `${member.name} needs you`,
            detail: member.status,
            href: `/workflow/${member.workflow_id}`,
            at: member.at,
        });
    }
    return items.sort((a, b) => KIND_RANK[a.kind] - KIND_RANK[b.kind]);
}

/** Heartbeats: active routines, soonest first; ones with no next run last. */
export function heartbeats(routines: RoutineResponse[]): RoutineResponse[] {
    return routines
        .filter((r) => r.is_active)
        .sort((a, b) => {
            const at = a.next_run_at ? Date.parse(a.next_run_at) : Infinity;
            const bt = b.next_run_at ? Date.parse(b.next_run_at) : Infinity;
            return at - bt;
        });
}

/** "just now", "5m ago", "in 2h", "3d ago". */
export function relative(iso: string | null | undefined, now: number = Date.now()): string {
    if (!iso) return "";
    const diff = Date.parse(iso) - now;
    if (Number.isNaN(diff)) return "";
    const abs = Math.abs(diff);
    const minutes = Math.round(abs / 60_000);
    if (minutes < 1) return "just now";
    const [value, unit] =
        minutes < 60 ? [minutes, "m"] : minutes < 60 * 24 ? [Math.round(minutes / 60), "h"] : [Math.round(minutes / 1440), "d"];
    return diff > 0 ? `in ${value}${unit}` : `${value}${unit} ago`;
}
