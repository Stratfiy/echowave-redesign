/**
 * A task on its own page (TB-2): the report signed off where it is read,
 * one thread where the board's own record reads as activity rather than a
 * person talking, a sub-task filed under this task, and a line that names
 * an agent sent as it is -- the server hands the task over.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    getOne: vi.fn(),
    list: vi.fn(),
    setStatus: vi.fn(),
    comment: vi.fn(),
    edit: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
}));
vi.mock("@/client/sdk.gen", () => ({
    getTaskApiV1TasksTaskIdGet: api.getOne,
    listTasksApiV1TasksGet: api.list,
    setTaskStatusApiV1TasksTaskIdStatusPost: api.setStatus,
    addCommentApiV1TasksTaskIdCommentsPost: api.comment,
    editTaskApiV1TasksTaskIdPatch: api.edit,
    createTaskApiV1TasksPost: api.create,
    deleteTaskApiV1TasksTaskIdDelete: api.remove,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

import { TaskPage } from "../TaskPage";
import type { Task } from "../tasks";

const task = (over: Partial<Task> = {}): Task => ({
    id: 12,
    number: 42,
    identifier: "DEC-42",
    title: "Follow up Mrs Lakshmi",
    brief: "Call about Tuesday.",
    status: "in_review",
    priority: "medium",
    from_workflow_id: null,
    from_name: null,
    assignee_workflow_id: 4,
    assignee_name: "Retention",
    assignee_user_id: null,
    assignee_user_name: null,
    parent_id: null,
    blocked_by: [],
    comment_count: 2,
    created_by: 42,
    due_at: null,
    result: "Booked Tuesday 5 pm.",
    workflow_run_id: 900,
    created_at: "2026-09-22T04:00:00Z",
    ...over,
});

const ok = (data: unknown) => ({ data, error: undefined });

beforeEach(() => {
    vi.clearAllMocks();
    api.getOne.mockResolvedValue(
        ok({
            ...task(),
            comments: [
                { id: 1, body: "Nithish moved this from To do to In review.", author_name: null, author_user_id: null, author_workflow_id: null, created_at: null },
                { id: 2, body: "Can you check the slot?", author_name: "Nithish", author_user_id: 42, author_workflow_id: null, created_at: null },
            ],
            subtasks: [task({ id: 13, number: 43, identifier: "DEC-43", title: "Send the reminder", status: "todo", parent_id: 12 })],
        }),
    );
    api.list.mockResolvedValue(
        ok({ tasks: [task()], bots: [{ id: 4, name: "Retention", handle: "retention" }], board: { enabled: true, people: [{ id: 42, name: "Nithish" }], me: 42 } }),
    );
    api.setStatus.mockResolvedValue(ok({}));
    api.comment.mockResolvedValue(ok({ id: 3, woke: "@retention" }));
    api.create.mockResolvedValue(ok({ id: 14 }));
});

describe("a task's own page", () => {
    it("signs off the report where it is read", async () => {
        render(<TaskPage taskId={12} />);
        expect(await screen.findByText("Booked Tuesday 5 pm.")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Sign off" }));
        await waitFor(() => expect(api.setStatus).toHaveBeenCalled());
        expect(api.setStatus.mock.calls[0][0]).toEqual({ path: { task_id: 12 }, body: { status: "done" } });
    });

    it("reads the board's record as activity and people's lines as a thread", async () => {
        render(<TaskPage taskId={12} />);
        const thread = await screen.findByRole("region", { name: "Thread" });
        const moved = within(thread).getByText(/moved this from To do to In review/);
        expect(moved.closest("li")?.className).toMatch(/text-xs/);
        const line = within(thread).getByText("Can you check the slot?");
        expect(line.closest("li")?.className).toMatch(/border/);
    });

    it("sends a line naming an agent as it is written", async () => {
        render(<TaskPage taskId={12} />);
        fireEvent.change(await screen.findByLabelText("Add to the thread"), { target: { value: "@retention try Wednesday" } });
        fireEvent.click(screen.getByRole("button", { name: "Send" }));
        await waitFor(() => expect(api.comment).toHaveBeenCalled());
        expect(api.comment.mock.calls[0][0]).toEqual({ path: { task_id: 12 }, body: { body: "@retention try Wednesday" } });
    });

    it("lists its sub-tasks and files a new one under it", async () => {
        render(<TaskPage taskId={12} />);
        const subs = await screen.findByRole("region", { name: "Sub-tasks" });
        expect(within(subs).getByText("Send the reminder")).toBeTruthy();
        fireEvent.click(within(subs).getByRole("button", { name: /add sub-task/i }));
        fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Confirm by SMS" } });
        fireEvent.click(screen.getByRole("button", { name: /file it/i }));
        await waitFor(() => expect(api.create).toHaveBeenCalled());
        expect(api.create.mock.calls[0][0].body.parent_id).toBe(12);
    });

    it("offers an agent's handle as @ is typed and sends the finished line", async () => {
        render(<TaskPage taskId={12} />);
        const box = (await screen.findByLabelText("Add to the thread")) as HTMLTextAreaElement;
        fireEvent.change(box, { target: { value: "@ret", selectionStart: 4 } });
        fireEvent.keyDown(box, { key: "Enter" });
        expect(box.value).toBe("@retention ");
        fireEvent.change(box, { target: { value: "@retention try Wednesday", selectionStart: 24 } });
        fireEvent.click(screen.getByRole("button", { name: "Send" }));
        await waitFor(() => expect(api.comment).toHaveBeenCalled());
        expect(api.comment.mock.calls[0][0].body).toEqual({ body: "@retention try Wednesday" });
    });

    it("adds a label from the properties column", async () => {
        api.edit.mockResolvedValue(ok({}));
        const { container } = render(<TaskPage taskId={12} />);
        await screen.findByRole("region", { name: "Thread" });
        const input = container.querySelector("#task-labels") as HTMLInputElement;
        fireEvent.change(input, { target: { value: "urgent" } });
        fireEvent.keyDown(input, { key: "Enter" });
        await waitFor(() => expect(api.edit).toHaveBeenCalled());
        expect(api.edit.mock.calls[0][0]).toEqual({ path: { task_id: 12 }, body: { labels: ["urgent"] } });
    });

    it("links to the run the agent did it in", async () => {
        render(<TaskPage taskId={12} />);
        const link = await screen.findByRole("link", { name: /agent's run/ });
        expect(link.getAttribute("href")).toBe("/workflow/4/run/900");
    });

    it("says so when the task is not here", async () => {
        api.getOne.mockResolvedValue({ data: undefined, error: { detail: "That task is not here." }, response: { status: 404 } });
        render(<TaskPage taskId={99} />);
        expect(await screen.findByRole("link", { name: "Back to the board" })).toBeTruthy();
    });
});
