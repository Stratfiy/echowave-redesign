/**
 * Decibyl's own routines on the Schedules board (KAN-156): a row with no bot
 * says "Decibyl", offers a test run, and is switched on or deleted on the
 * flat /routines path rather than a bot's.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listAll = vi.fn();
const armBot = vi.fn();
const armDecibyl = vi.fn();
const testDecibyl = vi.fn();
const deleteDecibyl = vi.fn();
const deleteBot = vi.fn();

vi.mock("@/client/sdk.gen", () => ({
    listAllRoutinesApiV1RoutinesGet: (...a: unknown[]) => listAll(...a),
    setActiveApiV1WorkflowsWorkflowIdRoutinesRoutineIdActivePost: (...a: unknown[]) => armBot(...a),
    setDecibylRoutineActiveApiV1RoutinesRoutineIdActivePost: (...a: unknown[]) => armDecibyl(...a),
    testDecibylRoutineApiV1RoutinesRoutineIdTestPost: (...a: unknown[]) => testDecibyl(...a),
    deleteDecibylRoutineApiV1RoutinesRoutineIdDelete: (...a: unknown[]) => deleteDecibyl(...a),
    deleteRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdDelete: (...a: unknown[]) => deleteBot(...a),
    updateRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdPut: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 42 }, loading: false }) }));
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
    usePathname: () => "/schedules",
}));

import { SchedulesBoard } from "../SchedulesBoard";

const routine = (overrides: Record<string, unknown>) => ({
    id: 1,
    workflow_id: 7,
    workflow_name: "Reception",
    name: "Morning brief",
    instruction: "Read the inbox.",
    cadence: "weekdays",
    anchor: "clock",
    at_minute: 540,
    offset_minutes: 0,
    weekday: 0,
    needs_apps: [],
    is_active: false,
    tested_at: "2026-09-27T00:00:00Z",
    may_arm: true,
    last_fired_at: null,
    last_skipped_reason: null,
    last_skipped_at: null,
    next_run_at: null,
    schedule_summary: "Every weekday at 09:00",
    ...overrides,
});

describe("Decibyl's own routines", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        listAll.mockResolvedValue({
            data: {
                routines: [
                    routine({}),
                    routine({ id: 2, workflow_id: null, workflow_name: "Decibyl", name: "Board summary" }),
                ],
            },
        });
        armDecibyl.mockResolvedValue({ data: {} });
        armBot.mockResolvedValue({ data: {} });
        testDecibyl.mockResolvedValue({ data: { started: true } });
        deleteDecibyl.mockResolvedValue({ data: {} });
    });

    it("names Decibyl and offers a test run only on its own rows", async () => {
        render(<SchedulesBoard />);
        const row = await screen.findByTestId("task-2");
        expect(row.textContent).toContain("Decibyl");
        expect(row.textContent).toContain("Test run");
        expect(row.textContent).not.toContain("Edit what it does");
        const botRow = screen.getByTestId("task-1");
        expect(botRow.textContent).toContain("Change when");
        expect(botRow.textContent).not.toContain("Test run");
    });

    it("tests, arms and deletes on the flat path, never a bot's", async () => {
        vi.spyOn(window, "confirm").mockReturnValue(true);
        render(<SchedulesBoard />);
        const row = await screen.findByTestId("task-2");
        fireEvent.click(screen.getAllByRole("button", { name: "Test run" })[0]);
        await waitFor(() => expect(testDecibyl).toHaveBeenCalledWith({ path: { routine_id: 2 } }));
        fireEvent.click(row.querySelector("button:nth-of-type(2)") as HTMLElement);
        await waitFor(() =>
            expect(armDecibyl).toHaveBeenCalledWith({ path: { routine_id: 2 }, query: { active: true } }),
        );
        expect(armBot).not.toHaveBeenCalled();
        fireEvent.click(screen.getAllByRole("button", { name: /Delete/ })[1]);
        await waitFor(() => expect(deleteDecibyl).toHaveBeenCalledWith({ path: { routine_id: 2 } }));
        expect(deleteBot).not.toHaveBeenCalled();
    });
});
