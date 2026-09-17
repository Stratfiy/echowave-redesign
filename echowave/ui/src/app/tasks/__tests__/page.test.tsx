/**
 * The schedule board can change the schedule.
 *
 * It shipped read-only, so the one screen that shows every task fired at the
 * wrong hour could not move it: you had to know which bot owned it and go
 * there. These guard the three things that made it a dead end -- when it
 * runs, on or off, gone -- and the one that would be a silent bug: the
 * endpoint replaces rather than patches, so a save about the hour must carry
 * the fields this screen never shows, or a routine that waits on Sheets and
 * fires half an hour early quietly becomes one that waits on nothing.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.hoisted(() => vi.fn());
const update = vi.hoisted(() => vi.fn());
const setActive = vi.hoisted(() => vi.fn());
const remove = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    listAllRoutinesApiV1RoutinesGet: list,
    updateRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdPut: update,
    setActiveApiV1WorkflowsWorkflowIdRoutinesRoutineIdActivePost: setActive,
    deleteRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdDelete: remove,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import TasksPage from "../page";

const routine = (over: Record<string, unknown> = {}) => ({
    id: 4,
    workflow_id: 7,
    workflow_name: "Front desk",
    name: "Morning summary",
    instruction: "Read yesterday's calls and message me.",
    cadence: "weekdays",
    anchor: "clock",
    at_minute: 9 * 60,
    offset_minutes: 30,
    weekday: 2,
    needs_apps: ["googlesheets"],
    is_active: true,
    next_run_at: null,
    schedule_summary: "Every weekday at 09:00",
    ...over,
});

beforeEach(() => {
    vi.clearAllMocks();
    list.mockResolvedValue({ data: { routines: [routine()] } });
    update.mockResolvedValue({ data: routine() });
    setActive.mockResolvedValue({ data: routine({ is_active: false }) });
    remove.mockResolvedValue({ data: undefined });
});

describe("the schedule board", () => {
    it("changes when a task runs", async () => {
        render(<TasksPage />);
        fireEvent.click(await screen.findByRole("button", { name: /change when/i }));
        fireEvent.change(screen.getByLabelText("Time"), { target: { value: "07:15" } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(update).toHaveBeenCalled());
        const call = update.mock.calls[0][0];
        expect(call.path).toEqual({ workflow_id: 7, routine_id: 4 });
        expect(call.body.at_minute).toBe(7 * 60 + 15);
    });

    it("carries the fields it does not show", async () => {
        render(<TasksPage />);
        fireEvent.click(await screen.findByRole("button", { name: /change when/i }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(update).toHaveBeenCalled());
        const body = update.mock.calls[0][0].body;
        expect(body.instruction).toBe("Read yesterday's calls and message me.");
        expect(body.offset_minutes).toBe(30);
        expect(body.needs_apps).toEqual(["googlesheets"]);
        expect(body.name).toBe("Morning summary");
    });

    it("switches a running task off", async () => {
        render(<TasksPage />);
        fireEvent.click(await screen.findByRole("button", { name: /switch off/i }));
        await waitFor(() => expect(setActive).toHaveBeenCalled());
        expect(setActive.mock.calls[0][0].query).toEqual({ active: false });
    });

    it("switches a stopped task on", async () => {
        list.mockResolvedValue({ data: { routines: [routine({ is_active: false })] } });
        render(<TasksPage />);
        fireEvent.click(await screen.findByRole("button", { name: /switch on/i }));
        await waitFor(() => expect(setActive).toHaveBeenCalled());
        expect(setActive.mock.calls[0][0].query).toEqual({ active: true });
    });

    it("asks before deleting, and does not delete when refused", async () => {
        const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
        render(<TasksPage />);
        fireEvent.click(await screen.findByRole("button", { name: /delete/i }));
        expect(confirm).toHaveBeenCalled();
        expect(remove).not.toHaveBeenCalled();
        confirm.mockRestore();
    });

    it("deletes when confirmed", async () => {
        const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
        render(<TasksPage />);
        fireEvent.click(await screen.findByRole("button", { name: /delete/i }));
        await waitFor(() => expect(remove).toHaveBeenCalled());
        expect(remove.mock.calls[0][0].path).toEqual({ workflow_id: 7, routine_id: 4 });
        confirm.mockRestore();
    });

    it("says why the server refused rather than looking like nothing happened", async () => {
        setActive.mockResolvedValue({
            error: { detail: "Run it once before switching it on." },
        });
        render(<TasksPage />);
        fireEvent.click(await screen.findByRole("button", { name: /switch off/i }));
        expect(
            await screen.findByText("Run it once before switching it on.")
        ).toBeTruthy();
    });

    it("still sends people to the bot for what a task does", async () => {
        render(<TasksPage />);
        const link = await screen.findByRole("link", { name: /edit what it does/i });
        expect(link.getAttribute("href")).toBe("/workflow/7");
    });
});
