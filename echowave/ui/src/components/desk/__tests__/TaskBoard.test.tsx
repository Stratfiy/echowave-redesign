/**
 * The board is a board (TB-1), and reads like paperclip's (TB-2).
 *
 * It opens as a list grouped by status, with an inbox beside it -- Mine,
 * Needs me, All tasks; the board view has the seven columns and a card
 * dragged to one is one status call; a task opens on its own page; a new
 * task is a button, not a standing form; a bot at work shows a live dot;
 * the filters narrow without asking the server; the Tasks door opens on the
 * board only when the server says it is on.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.hoisted(() => vi.fn());
const create = vi.hoisted(() => vi.fn());
const getOne = vi.hoisted(() => vi.fn());
const edit = vi.hoisted(() => vi.fn());
const comment = vi.hoisted(() => vi.fn());
const setStatus = vi.hoisted(() => vi.fn());
const remove = vi.hoisted(() => vi.fn());
const routines = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    listTasksApiV1TasksGet: list,
    createTaskApiV1TasksPost: create,
    getTaskApiV1TasksTaskIdGet: getOne,
    editTaskApiV1TasksTaskIdPatch: edit,
    addCommentApiV1TasksTaskIdCommentsPost: comment,
    setTaskStatusApiV1TasksTaskIdStatusPost: setStatus,
    deleteTaskApiV1TasksTaskIdDelete: remove,
    listAllRoutinesApiV1RoutinesGet: routines,
    updateRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdPut: vi.fn(),
    setActiveApiV1WorkflowsWorkflowIdRoutinesRoutineIdActivePost: vi.fn(),
    deleteRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdDelete: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 42 }, loading: false }) }));
const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }), usePathname: () => "/tasks" }));

import TasksPage from "@/app/tasks/page";

import { type BoardPayload, COLUMNS, type Task,TaskBoard } from "../TaskBoard";

const task = (over: Partial<Task> = {}): Task => ({
    id: 12,
    number: 42,
    identifier: "DEC-42",
    title: "Follow up Mrs Lakshmi",
    brief: "Call about Tuesday.",
    status: "todo",
    priority: "medium",
    from_workflow_id: 3,
    from_name: "Front desk",
    assignee_workflow_id: 4,
    assignee_name: "Retention",
    assignee_user_id: null,
    assignee_user_name: null,
    parent_id: null,
    blocked_by: [],
    comment_count: 0,
    created_by: null,
    due_at: null,
    result: null,
    workflow_run_id: null,
    created_at: "2026-09-22T04:00:00Z",
    ...over,
});

const payload = (tasks: Task[]): BoardPayload => ({
    tasks,
    statuses: COLUMNS.map((c) => c.id),
    bots: [{ id: 4, name: "Retention", handle: "retention" }],
    board: {
        enabled: true,
        priorities: ["critical", "high", "medium", "low"],
        prefix: "DEC",
        people: [{ id: 42, name: "Nithish" }],
        me: 42,
    },
});

const tabs = [{ href: "/tasks", label: "Tasks" }];

beforeEach(() => {
    vi.clearAllMocks();
    list.mockResolvedValue({ data: payload([task()]) });
    setStatus.mockResolvedValue({ data: task({ status: "in_progress" }) });
    getOne.mockResolvedValue({ data: { ...task(), comments: [] } });
    edit.mockResolvedValue({ data: task() });
    comment.mockResolvedValue({ data: { id: 1 } });
    create.mockResolvedValue({ data: task() });
    routines.mockResolvedValue({ data: { routines: [] } });
});

const toBoard = () => fireEvent.click(screen.getByRole("button", { name: "Board" }));

describe("the list", () => {
    it("opens as a list grouped by status", () => {
        const done = task({ id: 2, number: 2, identifier: "DEC-2", title: "Sent the quote", status: "done" });
        render(<TaskBoard initial={payload([task(), done])} tabs={tabs} />);
        expect(screen.getByRole("table", { name: "Tasks" })).toBeTruthy();
        expect(within(screen.getByRole("region", { name: "To do" })).getByText("Follow up Mrs Lakshmi")).toBeTruthy();
        expect(within(screen.getByRole("region", { name: "Done" })).getByText("Sent the quote")).toBeTruthy();
        expect(screen.queryByTestId("column-todo")).toBeNull();
    });

    it("regroups by priority without asking the server", () => {
        render(<TaskBoard initial={payload([task({ priority: "high" })])} tabs={tabs} />);
        fireEvent.change(screen.getByLabelText("Group by"), { target: { value: "priority" } });
        expect(within(screen.getByRole("region", { name: "High" })).getByText("Follow up Mrs Lakshmi")).toBeTruthy();
        expect(list).not.toHaveBeenCalled();
    });

    it("opens a task on its own page", () => {
        render(<TaskBoard initial={payload([task()])} tabs={tabs} />);
        fireEvent.click(screen.getByText("Follow up Mrs Lakshmi"));
        expect(push).toHaveBeenCalledWith("/tasks/12");
    });

    it("shows a live dot while an agent works a task, and not otherwise", () => {
        const live = task({ status: "in_progress" });
        const { rerender } = render(<TaskBoard initial={payload([live])} tabs={tabs} />);
        expect(screen.getByLabelText("An agent is working on it")).toBeTruthy();
        rerender(<TaskBoard key="b" initial={payload([task({ status: "in_progress", assignee_workflow_id: null, assignee_name: null })])} tabs={tabs} />);
        expect(screen.queryByLabelText("An agent is working on it")).toBeNull();
    });

    it("searches titles and identifiers", () => {
        const other = task({ id: 2, number: 7, identifier: "DEC-7", title: "Renew the lease" });
        render(<TaskBoard initial={payload([task(), other])} tabs={tabs} />);
        fireEvent.change(screen.getByLabelText("Search tasks"), { target: { value: "dec-7" } });
        expect(screen.getByText("Renew the lease")).toBeTruthy();
        expect(screen.queryByText("Follow up Mrs Lakshmi")).toBeNull();
    });
});

describe("labels on the board", () => {
    it("show on a row and narrow the list by one", () => {
        const tagged = task({ id: 2, number: 2, identifier: "DEC-2", title: "Chase the invoice", labels: ["Billing"] });
        const withLabels = { ...payload([task(), tagged]), board: { ...payload([]).board, labels: ["Billing"] } };
        render(<TaskBoard initial={withLabels} tabs={tabs} />);
        const row = screen.getByText("Chase the invoice").closest("li")!;
        expect(within(row).getByText("Billing")).toBeTruthy();
        fireEvent.change(screen.getByLabelText("Filter by label"), { target: { value: "Billing" } });
        expect(screen.getByText("Chase the invoice")).toBeTruthy();
        expect(screen.queryByText("Follow up Mrs Lakshmi")).toBeNull();
    });

    it("are filed with a new task", async () => {
        render(<TaskBoard initial={payload([])} tabs={tabs} />);
        fireEvent.click(screen.getByRole("button", { name: /new task/i }));
        fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Renew lease" } });
        const labels = screen.getByLabelText("Labels");
        fireEvent.change(labels, { target: { value: "admin" } });
        fireEvent.keyDown(labels, { key: "Enter" });
        fireEvent.click(screen.getByRole("button", { name: /file it/i }));
        await waitFor(() => expect(create).toHaveBeenCalled());
        expect(create.mock.calls[0][0].body.labels).toEqual(["admin"]);
    });

    it("offer no label filter while the board has none", () => {
        render(<TaskBoard initial={payload([task()])} tabs={tabs} />);
        expect(screen.queryByLabelText("Filter by label")).toBeNull();
    });
});

describe("sub-task progress", () => {
    it("shows how far a parent's sub-tasks have got, on the list and the board", () => {
        const parent = task({ id: 1, number: 1, identifier: "DEC-1", title: "Open the Adyar branch" });
        const done = task({ id: 2, number: 2, identifier: "DEC-2", title: "Sign the lease", parent_id: 1, status: "done" });
        const open = task({ id: 3, number: 3, identifier: "DEC-3", title: "Hire a receptionist", parent_id: 1 });
        render(<TaskBoard initial={payload([parent, done, open])} tabs={tabs} />);
        const row = screen.getByText("Open the Adyar branch").closest("li")!;
        expect(within(row).getByRole("progressbar", { name: "Sub-tasks done" }).getAttribute("aria-valuenow")).toBe("1");
        expect(within(row).getByText("1/2")).toBeTruthy();
        toBoard();
        expect(within(screen.getByTestId("task-1")).getByText("1/2")).toBeTruthy();
    });
});

describe("the inbox", () => {
    const mine = task({ id: 1, number: 1, identifier: "DEC-1", title: "Approve the refund", assignee_workflow_id: null, assignee_name: null, assignee_user_id: 42, assignee_user_name: "Nithish" });
    const review = task({ id: 2, number: 2, identifier: "DEC-2", title: "Report waiting", status: "in_review", result: "Done it." });
    const other = task({ id: 3, number: 3, identifier: "DEC-3", title: "Someone else's" });

    it("counts and narrows to what is mine and what needs me", () => {
        render(<TaskBoard initial={payload([mine, review, other])} tabs={tabs} />);
        const inbox = screen.getByRole("navigation", { name: "Inbox" });
        expect(within(inbox).getByRole("button", { name: /Mine\s*1/ })).toBeTruthy();
        expect(within(inbox).getByRole("button", { name: /Needs me\s*1/ })).toBeTruthy();
        expect(within(inbox).getByRole("button", { name: /All tasks\s*3/ })).toBeTruthy();

        fireEvent.click(within(inbox).getByRole("button", { name: /Needs me/ }));
        expect(screen.getByText("Report waiting")).toBeTruthy();
        expect(screen.queryByText("Approve the refund")).toBeNull();

        fireEvent.click(within(inbox).getByRole("button", { name: /^Mine/ }));
        expect(screen.getByText("Approve the refund")).toBeTruthy();
        expect(screen.queryByText("Someone else's")).toBeNull();
    });
});

describe("the board view", () => {
    it("has paperclip's seven columns", () => {
        render(<TaskBoard initial={payload([task()])} tabs={tabs} />);
        toBoard();
        for (const label of ["Backlog", "To do", "In progress", "In review", "Done", "Blocked", "Cancelled"]) {
            expect(screen.getByRole("region", { name: label })).toBeTruthy();
        }
        expect(within(screen.getByTestId("column-todo")).getByText("Follow up Mrs Lakshmi")).toBeTruthy();
        expect(screen.getByText("DEC-42")).toBeTruthy();
    });

    it("moves a dropped card with one status call", async () => {
        render(<TaskBoard initial={payload([task()])} tabs={tabs} />);
        toBoard();
        const card = screen.getByTestId("task-12");
        const data = { setData: vi.fn(), getData: vi.fn(() => "12") };
        fireEvent.dragStart(card, { dataTransfer: data });
        const column = screen.getByTestId("column-in_progress");
        fireEvent.dragOver(column, { dataTransfer: data });
        fireEvent.drop(column, { dataTransfer: data });
        await waitFor(() => expect(setStatus).toHaveBeenCalledTimes(1));
        expect(setStatus.mock.calls[0][0]).toEqual({ path: { task_id: 12 }, body: { status: "in_progress" } });
    });

    it("shows the agent's report on a card in review", () => {
        const reviewed = task({ status: "in_review", result: "Booked Tuesday 5 pm." });
        render(<TaskBoard initial={payload([reviewed])} tabs={tabs} />);
        toBoard();
        expect(within(screen.getByTestId("column-in_review")).getByText("Booked Tuesday 5 pm.")).toBeTruthy();
    });

    it("narrows by owner without asking the server", () => {
        const teams = task({ id: 1, number: 1, identifier: "DEC-1", title: "The team's", assignee_workflow_id: null, assignee_name: null });
        const theirs = task({ id: 2, number: 2, identifier: "DEC-2", title: "Theirs" });
        render(<TaskBoard initial={payload([teams, theirs])} tabs={tabs} />);
        toBoard();
        fireEvent.change(screen.getByLabelText("Filter by owner"), { target: { value: "bot:4" } });
        expect(screen.queryByText("The team's")).toBeNull();
        expect(screen.getByText("Theirs")).toBeTruthy();
        expect(list).not.toHaveBeenCalled();
    });
});

describe("a new task", () => {
    it("is a button, not a form standing over the board", () => {
        render(<TaskBoard initial={payload([])} tabs={tabs} />);
        expect(screen.queryByLabelText("Title")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: /new task/i }));
        expect(screen.getByLabelText("Title")).toBeTruthy();
    });

    it("files one for a person with a priority", async () => {
        render(<TaskBoard initial={payload([])} tabs={tabs} />);
        fireEvent.click(screen.getByRole("button", { name: /new task/i }));
        fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Approve the refund" } });
        fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "user:42" } });
        fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "high" } });
        fireEvent.click(screen.getByRole("button", { name: /file it/i }));
        await waitFor(() => expect(create).toHaveBeenCalled());
        const body = create.mock.calls[0][0].body;
        expect(body.assignee).toBe("user:42");
        expect(body.priority).toBe("high");
        expect(body.backlog).toBe(false);
        expect(body.parent_id).toBeUndefined();
    });
});

describe("the Tasks door", () => {
    it("opens on the board when the server says it is on", async () => {
        render(<TasksPage />);
        expect(await screen.findByRole("navigation", { name: "Inbox" })).toBeTruthy();
    });

    it("opens on the schedules when it is off", async () => {
        list.mockResolvedValue({ data: { tasks: [], board: { enabled: false } } });
        render(<TasksPage />);
        await waitFor(() => expect(routines).toHaveBeenCalled());
        expect(screen.queryByRole("navigation", { name: "Inbox" })).toBeNull();
    });
});
