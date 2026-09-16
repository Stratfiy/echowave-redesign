/**
 * The screen the routines runtime shipped without.
 *
 * Guarded: a routine that has never been run cannot be switched on and says
 * why; running it once is offered; a skipped run is shown rather than hidden,
 * because a routine that silently did not run is indistinguishable from one
 * that never existed; and switching on sends the state as a query, which is
 * what the endpoint takes.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.hoisted(() => vi.fn());
const setActive = vi.hoisted(() => vi.fn());
const testRun = vi.hoisted(() => vi.fn());
const create = vi.hoisted(() => vi.fn());
const remove = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    listRoutinesApiV1WorkflowsWorkflowIdRoutinesGet: list,
    setActiveApiV1WorkflowsWorkflowIdRoutinesRoutineIdActivePost: setActive,
    testRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdTestPost: testRun,
    createRoutineApiV1WorkflowsWorkflowIdRoutinesPost: create,
    deleteRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdDelete: remove,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import { RoutinesPanel } from "../RoutinesPanel";

const routine = (over: Record<string, unknown> = {}) => ({
    id: 4,
    workflow_id: 7,
    name: "Morning summary",
    instruction: "Read yesterday's calls and message me.",
    cadence: "weekdays",
    anchor: "opening",
    at_minute: 0,
    offset_minutes: 0,
    weekday: 0,
    needs_apps: [],
    is_active: false,
    tested_at: null,
    may_arm: false,
    last_fired_at: null,
    last_skipped_reason: null,
    last_skipped_at: null,
    next_run_at: null,
    schedule_summary: "Every weekday when you open",
    ...over,
});

beforeEach(() => {
    vi.clearAllMocks();
    list.mockResolvedValue({ data: { routines: [routine()] } });
    setActive.mockResolvedValue({ data: routine({ is_active: true }) });
    testRun.mockResolvedValue({ data: { started: true, detail: "Running once now." } });
});

describe("RoutinesPanel", () => {
    it("shows the schedule in words", async () => {
        render(<RoutinesPanel workflowId={7} />);
        expect(await screen.findByText("Every weekday when you open")).toBeTruthy();
        expect(screen.getByText("Morning summary")).toBeTruthy();
    });

    it("will not arm a routine that has never been run, and says why", async () => {
        render(<RoutinesPanel workflowId={7} />);
        const button = await screen.findByRole("button", { name: "Switch on" });
        expect((button as HTMLButtonElement).disabled).toBe(true);
        expect(screen.getByText("Run it once before switching it on.")).toBeTruthy();
    });

    it("arms one that has been tested, as a query the endpoint takes", async () => {
        list.mockResolvedValue({ data: { routines: [routine({ may_arm: true, tested_at: "2026-09-16T00:00:00Z" })] } });
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: "Switch on" }));
        await waitFor(() => expect(setActive).toHaveBeenCalled());
        expect(setActive.mock.calls[0][0].query).toEqual({ active: true });
        expect(setActive.mock.calls[0][0].body).toBeUndefined();
    });

    it("switching off is never refused", async () => {
        list.mockResolvedValue({ data: { routines: [routine({ is_active: true, may_arm: false })] } });
        render(<RoutinesPanel workflowId={7} />);
        const off = await screen.findByRole("button", { name: "Switch off" });
        expect((off as HTMLButtonElement).disabled).toBe(false);
    });

    it("runs one once and reports what the server said", async () => {
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: "Run once" }));
        await waitFor(() => expect(testRun).toHaveBeenCalled());
        expect(await screen.findByText("Running once now.")).toBeTruthy();
    });

    it("shows why a run was skipped", async () => {
        list.mockResolvedValue({
            data: {
                routines: [
                    routine({
                        is_active: true,
                        may_arm: true,
                        last_skipped_reason: "Gmail is not connected",
                        last_skipped_at: "2026-09-16T03:00:00Z",
                    }),
                ],
            },
        });
        render(<RoutinesPanel workflowId={7} />);
        expect(await screen.findByText(/Gmail is not connected/)).toBeTruthy();
    });

    it("says nothing is scheduled rather than showing an empty box", async () => {
        list.mockResolvedValue({ data: { routines: [] } });
        render(<RoutinesPanel workflowId={7} />);
        expect(await screen.findByText(/Nothing scheduled/)).toBeTruthy();
    });

    it("a list that cannot be read says so, and does not read as empty", async () => {
        list.mockResolvedValue({ error: { detail: "nope" } });
        render(<RoutinesPanel workflowId={7} />);
        expect(await screen.findByText(/could not be read/)).toBeTruthy();
        expect(screen.queryByText(/Nothing scheduled/)).toBeNull();
    });
});
