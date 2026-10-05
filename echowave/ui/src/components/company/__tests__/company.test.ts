import { describe, expect, it } from "vitest";

import type { BudgetPolicyResponse, TeamMember } from "@/client/types.gen";
import type { Task } from "@/components/desk/tasks";

import { buildOrg, headline, heartbeats, inbox, NO_TEAM, relative } from "../company";

const member = (id: number, name: string, tone = "idle", extra: Partial<TeamMember> = {}): TeamMember => ({
    workflow_id: id,
    workflow_uuid: null,
    name,
    is_live: tone !== "paused",
    status: `${name} status`,
    tone,
    at: "2026-10-05T10:00:00Z",
    calls: 1,
    answered: 1,
    outcomes: 0,
    failures: 0,
    last_action: null,
    ...extra,
});

const task = (id: number, status: string, extra: Partial<Task> = {}): Task => ({
    id,
    number: id,
    identifier: null,
    title: `Task ${id}`,
    brief: "",
    status,
    priority: "medium",
    from_workflow_id: null,
    from_name: null,
    assignee_workflow_id: null,
    assignee_name: null,
    assignee_user_id: null,
    assignee_user_name: null,
    parent_id: null,
    blocked_by: [],
    comment_count: 0,
    created_by: null,
    due_at: null,
    result: null,
    workflow_run_id: null,
    created_at: "2026-10-05T09:00:00Z",
    ...extra,
});

const policy = (workflow_id: number | null, amount: number, spent: number): BudgetPolicyResponse => ({
    id: workflow_id ?? 0,
    workflow_id,
    window_kind: "calendar_month",
    amount_credits: amount,
    warn_percent: 80,
    hard_stop: true,
    spent_credits: spent,
    window_start: "2026-10-01T00:00:00Z",
    window_end: null,
    exhausted: spent >= amount,
    warned: spent >= amount * 0.8,
});

const wf = (id: number, folder_id: number | null, handle: string | null = null) => ({
    id,
    name: `wf${id}`,
    status: "active",
    created_at: "2026-09-01T00:00:00Z",
    total_runs: 0,
    folder_id,
    handle,
});

describe("buildOrg", () => {
    it("places agents in their folder's team, the unfiled last, and drops empty teams", () => {
        const teams = buildOrg({
            members: [member(1, "Ava"), member(2, "Ben", "attention"), member(3, "Cy")],
            workflows: [wf(1, 10, "ava"), wf(2, 10), wf(3, null)],
            folders: [
                { id: 10, name: "Sales", created_at: "" },
                { id: 11, name: "Empty", created_at: "" },
            ],
            policies: [policy(1, 100, 25)],
            tasks: [task(1, "todo", { assignee_workflow_id: 1 }), task(2, "done", { assignee_workflow_id: 1 })],
        });
        expect(teams.map((t) => t.name)).toEqual(["Sales", NO_TEAM]);
        // attention sorts first within a team
        expect(teams[0].agents.map((a) => a.name)).toEqual(["Ben", "Ava"]);
        const ava = teams[0].agents[1];
        expect(ava.handle).toBe("ava");
        expect(ava.openTasks).toBe(1);
        expect(ava.budget).toMatchObject({ spent: 25, limit: 100, percent: 25, exhausted: false });
        expect(teams[1].agents[0].budget).toBeNull();
    });

    it("puts an agent the workflow list does not know under no team", () => {
        const teams = buildOrg({ members: [member(9, "Zed")], workflows: [], folders: [], policies: [], tasks: [] });
        expect(teams).toHaveLength(1);
        expect(teams[0].name).toBe(NO_TEAM);
    });
});

describe("headline", () => {
    it("counts agents and tasks, and prefers the workspace-wide cap", () => {
        const h = headline(
            [member(1, "A", "working"), member(2, "B", "attention")],
            [task(1, "in_progress"), task(2, "blocked"), task(3, "in_review"), task(4, "done")],
            [policy(null, 500, 120), policy(1, 100, 90)],
        );
        expect(h).toMatchObject({ agents: 2, working: 1, needsYou: 1, openTasks: 3, inProgress: 1, blocked: 1, inReview: 1 });
        expect(h.spent).toBe(120);
        expect(h.limit).toBe(500);
    });

    it("sums the agents' caps without a workspace cap, and has no limit without any", () => {
        expect(headline([], [], [policy(1, 100, 10), policy(2, 50, 5)])).toMatchObject({ spent: 15, limit: 150 });
        expect(headline([], [], []).limit).toBeNull();
    });
});

describe("inbox", () => {
    it("orders budget stops, then blocked, reviews and agents asking for help", () => {
        const items = inbox({
            incidents: [
                { id: 1, policy_id: 1, workflow_id: 2, threshold: "warn", window_start: "", window_end: null, limit_credits: 100, observed_credits: 85, status: "open", created_at: null },
                { id: 2, policy_id: 1, workflow_id: 2, threshold: "hard", window_start: "", window_end: null, limit_credits: 100, observed_credits: 101, status: "open", created_at: null },
                { id: 3, policy_id: 1, workflow_id: 2, threshold: "hard", window_start: "", window_end: null, limit_credits: 100, observed_credits: 101, status: "dismissed", created_at: null },
            ],
            tasks: [task(5, "in_review", { assignee_name: "Ava" }), task(6, "blocked"), task(7, "todo")],
            members: [member(2, "Ben", "attention"), member(3, "Cy", "working")],
            nameOf: (id) => (id === 2 ? "Ben" : "?"),
        });
        expect(items.map((i) => i.key)).toEqual(["budget-2", "budget-1", "task-6", "task-5", "agent-2"]);
        expect(items[0].title).toBe("Ben hit its budget");
        expect(items[0].incidentId).toBe(2);
        expect(items[3].detail).toBe("Ava sent a report to sign off");
        expect(items[2].href).toBe("/tasks/6");
    });
});

describe("heartbeats", () => {
    it("keeps active routines, soonest first, unscheduled last", () => {
        const base = { instruction: "", cadence: "daily", anchor: "", at_minute: 0, offset_minutes: 0, weekday: 0, needs_apps: [] };
        const list = heartbeats([
            { ...base, id: 1, name: "late", is_active: true, next_run_at: "2026-10-06T10:00:00Z" },
            { ...base, id: 2, name: "off", is_active: false, next_run_at: "2026-10-05T10:00:00Z" },
            { ...base, id: 3, name: "none", is_active: true, next_run_at: null },
            { ...base, id: 4, name: "soon", is_active: true, next_run_at: "2026-10-05T11:00:00Z" },
        ]);
        expect(list.map((r) => r.name)).toEqual(["soon", "late", "none"]);
    });
});

describe("relative", () => {
    const now = Date.parse("2026-10-05T12:00:00Z");
    it.each([
        ["2026-10-05T12:00:20Z", "just now"],
        ["2026-10-05T11:55:00Z", "5m ago"],
        ["2026-10-05T14:00:00Z", "in 2h"],
        ["2026-10-02T12:00:00Z", "3d ago"],
        [null, ""],
    ])("reads %s as %s", (iso, text) => {
        expect(relative(iso, now)).toBe(text);
    });
});
