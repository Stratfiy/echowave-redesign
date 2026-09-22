/**
 * The board is a board (TB-1).
 *
 * Seven columns from paperclip's issue model; a card dragged to a column is
 * one status call; the review column carries the agent's report and a
 * person signs it off from the card; the filters narrow the board without
 * asking the server; the Tasks door opens on the board only when the server
 * says it is on.
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

describe("the board", () => {
    it("has paperclip's seven columns", () => {
        render(<TaskBoard initial={payload([task()])} tabs={tabs} />);
        for (const label of ["Backlog", "To do", "In progress", "In review", "Done", "Blocked", "Cancelled"]) {
            expect(screen.getByRole("region", { name: label })).toBeTruthy();
        }
        expect(within(screen.getByTestId("column-todo")).getByText("Follow up Mrs Lakshmi")).toBeTruthy();
        expect(screen.getByText("DEC-42")).toBeTruthy();
    });

    it("moves a dropped card with one status call", async () => {
        render(<TaskBoard initial={payload([task()])} tabs={tabs} />);
        const card = screen.getByTestId("task-12");
        const data = { setData: vi.fn(), getData: vi.fn(() => "12") };
        fireEvent.dragStart(card, { dataTransfer: data });
        const column = screen.getByTestId("column-in_progress");
        fireEvent.dragOver(column, { dataTransfer: data });
        fireEvent.drop(column, { dataTransfer: data });
        await waitFor(() => expect(setStatus).toHaveBeenCalledTimes(1));
        expect(setStatus.mock.calls[0][0]).toEqual({ path: { task_id: 12 }, body: { status: "in_progress" } });
    });

    it("shows the report in review and signs it off from the card", async () => {
        const reviewed = task({ status: "in_review", result: "Booked Tuesday 5 pm." });
        list.mockResolvedValue({ data: payload([reviewed]) });
        getOne.mockResolvedValue({
            data: { ...reviewed, comments: [{ id: 1, body: "Booked Tuesday 5 pm.", author_name: "Retention", author_workflow_id: 4, author_user_id: null, created_at: null }] },
        });
        render(<TaskBoard initial={payload([reviewed])} tabs={tabs} />);
        const column = screen.getByTestId("column-in_review");
        expect(within(column).getByText("Booked Tuesday 5 pm.")).toBeTruthy();
        fireEvent.click(within(column).getByText("Follow up Mrs Lakshmi"));
        await screen.findByText("On the card");
        fireEvent.click(screen.getByRole("button", { name: "Sign off" }));
        await waitFor(() => expect(setStatus).toHaveBeenCalled());
        expect(setStatus.mock.calls[0][0].body).toEqual({ status: "done" });
    });

    it("files a task for a person with a priority", async () => {
        const { container } = render(<TaskBoard initial={payload([])} tabs={tabs} />);
        fireEvent.change(screen.getByLabelText("New task"), { target: { value: "Approve the refund" } });
        fireEvent.change(container.querySelector("#task-assignee")!, { target: { value: "user:42" } });
        fireEvent.change(container.querySelector("#task-priority")!, { target: { value: "high" } });
        fireEvent.click(screen.getByRole("button", { name: /file it/i }));
        await waitFor(() => expect(create).toHaveBeenCalled());
        const body = create.mock.calls[0][0].body;
        expect(body.assignee).toBe("user:42");
        expect(body.priority).toBe("high");
        expect(body.backlog).toBe(false);
    });

    it("narrows by owner and by who filed it without asking the server", () => {
        const mine = task({ id: 1, number: 1, identifier: "DEC-1", title: "Mine", created_by: 42, assignee_workflow_id: null, assignee_name: null });
        const theirs = task({ id: 2, number: 2, identifier: "DEC-2", title: "Theirs", created_by: 7 });
        render(<TaskBoard initial={payload([mine, theirs])} tabs={tabs} />);
        fireEvent.change(screen.getByLabelText("Filter by owner"), { target: { value: "bot:4" } });
        expect(screen.queryByText("Mine")).toBeNull();
        expect(screen.getByText("Theirs")).toBeTruthy();
        fireEvent.change(screen.getByLabelText("Filter by owner"), { target: { value: "all" } });
        fireEvent.click(screen.getByLabelText("Filed by me"));
        expect(screen.getByText("Mine")).toBeTruthy();
        expect(screen.queryByText("Theirs")).toBeNull();
        expect(list).not.toHaveBeenCalled();
    });

    it("keeps the list as a toggle", () => {
        render(<TaskBoard initial={payload([task()])} tabs={tabs} />);
        fireEvent.click(screen.getByRole("button", { name: "List" }));
        expect(screen.getByRole("table")).toBeTruthy();
        expect(screen.queryByTestId("column-todo")).toBeNull();
    });
});

describe("the Tasks door", () => {
    it("opens on the board when the server says it is on", async () => {
        render(<TasksPage />);
        expect(await screen.findByTestId("column-in_review")).toBeTruthy();
    });

    it("opens on the schedules when it is off", async () => {
        list.mockResolvedValue({ data: { tasks: [], board: { enabled: false } } });
        render(<TasksPage />);
        await waitFor(() => expect(routines).toHaveBeenCalled());
        expect(screen.queryByTestId("column-in_review")).toBeNull();
    });
});
