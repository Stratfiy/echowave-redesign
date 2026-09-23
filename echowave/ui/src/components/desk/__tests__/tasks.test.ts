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

describe("labels", () => {
    it("give the same word the same colour whatever its case", async () => {
        const { labelTone } = await import("../tasks");
        expect(labelTone("Billing")).toBe(labelTone("billing"));
    });

    it("match a filter case-insensitively, and are searched", async () => {
        const { hasLabel, matches } = await import("../tasks");
        expect(hasLabel(t({ labels: ["VIP"] }), "vip")).toBe(true);
        expect(hasLabel(t({}), "vip")).toBe(false);
        expect(matches(t({ labels: ["follow-up"] }), "follow")).toBe(true);
    });
});

describe("@mentions", () => {
    it("find the word being typed at the caret, and not an email address", async () => {
        const { mentionAt } = await import("../tasks");
        expect(mentionAt("ask @bil", 8)).toEqual({ start: 4, query: "bil" });
        expect(mentionAt("@", 1)).toEqual({ start: 0, query: "" });
        expect(mentionAt("mail a@bil", 10)).toBeNull();
        expect(mentionAt("ask @bil then", 13)).toBeNull();
    });

    it("offer handles that start with the query first, then ones that contain it", async () => {
        const { mentionChoices } = await import("../tasks");
        const bots = [
            { id: 1, name: "Chase payments", handle: "collections" },
            { id: 2, name: "Billing", handle: "billing" },
            { id: 3, name: "No handle", handle: null },
        ];
        expect(mentionChoices(bots, "bil").map((b) => b.id)).toEqual([2]);
        expect(mentionChoices(bots, "ll").map((b) => b.id)).toEqual([1, 2]);
        expect(mentionChoices(bots, "").map((b) => b.id)).toEqual([1, 2]);
    });

    it("write the handle in place of the partial word", async () => {
        const { insertMention } = await import("../tasks");
        expect(insertMention("hi @bi there", 3, 6, "billing")).toEqual({ text: "hi @billing  there", caret: 12 });
        expect(insertMention("@b", 0, 2, "billing")).toEqual({ text: "@billing ", caret: 9 });
    });
});
