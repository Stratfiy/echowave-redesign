/** The inbox and grouping rules, without a screen (TB-2). */
import { describe, expect, it } from "vitest";

import { groupTasks, inInbox, parseWaitsOn, type Task } from "../tasks";

const t = (over: Partial<Task>): Task => ({
    id: 1, number: 1, identifier: "DEC-1", title: "x", brief: "", status: "todo", priority: "medium",
    from_workflow_id: null, from_name: null, assignee_workflow_id: null, assignee_name: null,
    assignee_user_id: null, assignee_user_name: null, parent_id: null, blocked_by: [],
    comment_count: 0, created_by: null, due_at: null, result: null, workflow_run_id: null, created_at: null,
    ...over,
});

describe("the inbox", () => {
    it("counts what I own or filed, while it is open", () => {
        expect(inInbox(t({ assignee_user_id: 42 }), "mine", 42)).toBe(true);
        expect(inInbox(t({ created_by: 42 }), "mine", 42)).toBe(true);
        expect(inInbox(t({ created_by: 42, status: "done" }), "mine", 42)).toBe(false);
        expect(inInbox(t({ assignee_user_id: 7 }), "mine", 42)).toBe(false);
    });

    it("puts the review queue and the stuck work in Needs me, whoever owns it", () => {
        expect(inInbox(t({ status: "in_review", assignee_workflow_id: 4 }), "needs_me", 42)).toBe(true);
        expect(inInbox(t({ status: "blocked" }), "needs_me", 42)).toBe(true);
        expect(inInbox(t({ status: "in_progress" }), "needs_me", 42)).toBe(false);
    });
});

describe("grouping", () => {
    it("keeps the column order and never drops a status it does not know", () => {
        const groups = groupTasks([t({ id: 1, status: "done" }), t({ id: 2, status: "todo" }), t({ id: 3, status: "archived" })], "status");
        expect(groups.map((g) => g.key)).toEqual(["todo", "done", "archived"]);
    });

    it("groups by owner by name", () => {
        const groups = groupTasks([t({ assignee_name: "Retention", assignee_workflow_id: 4 }), t({ id: 2 })], "owner");
        expect(groups.map((g) => g.label).sort()).toEqual(["Retention", "The team"]);
    });
});

it("reads waits-on in any of the ways a person writes it", () => {
    const tasks = [t({ id: 50, number: 12 }), t({ id: 51, number: 14 })];
    expect(parseWaitsOn("DEC-12, #14 99", tasks)).toEqual([50, 51, 99]);
});
