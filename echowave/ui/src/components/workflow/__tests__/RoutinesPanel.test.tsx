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
const update = vi.hoisted(() => vi.fn());
const remove = vi.hoisted(() => vi.fn());
const connectors = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    listRoutinesApiV1WorkflowsWorkflowIdRoutinesGet: list,
    setActiveApiV1WorkflowsWorkflowIdRoutinesRoutineIdActivePost: setActive,
    testRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdTestPost: testRun,
    createRoutineApiV1WorkflowsWorkflowIdRoutinesPost: create,
    updateRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdPut: update,
    deleteRoutineApiV1WorkflowsWorkflowIdRoutinesRoutineIdDelete: remove,
    listConnectorsApiV1ConnectorsGet: connectors,
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
    connectors.mockResolvedValue({
        data: {
            available: true,
            connected_count: 1,
            groups: [
                {
                    group: "Email",
                    connectors: [
                        { slug: "gmail", name: "Gmail", connected: true },
                        { slug: "outlook", name: "Outlook", connected: false },
                    ],
                },
            ],
        },
    });
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

describe("editing a routine", () => {
    /**
     * The endpoint takes a whole routine, and two of its fields are not on
     * this form. A save about the time would otherwise clear both, and
     * neither loss shows up anywhere: the routine keeps running, just at a
     * different minute and without the connectors it cannot work without.
     */
    beforeEach(() => {
        update.mockResolvedValue({ data: routine() });
    });

    it("fills the form from the routine rather than opening it empty", async () => {
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit Morning summary" }));
        expect((screen.getByLabelText("Call it") as HTMLInputElement).value).toBe(
            "Morning summary",
        );
        expect((screen.getByLabelText("What it should do") as HTMLTextAreaElement).value).toBe(
            "Read yesterday's calls and message me.",
        );
    });

    it("puts the stored minute back into the time field", async () => {
        list.mockResolvedValue({ data: { routines: [routine({ anchor: "clock", at_minute: 545 })] } });
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit Morning summary" }));
        // 545 minutes past midnight is 09:05, not "545".
        expect((screen.getByLabelText("Time") as HTMLInputElement).value).toBe("09:05");
    });

    it("updates rather than creating a second routine", async () => {
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit Morning summary" }));
        fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));
        await waitFor(() => expect(update).toHaveBeenCalled());
        expect(create).not.toHaveBeenCalled();
        expect(update.mock.calls[0][0].path).toEqual({ workflow_id: 7, routine_id: 4 });
    });

    it("keeps the connectors the routine needs, folded to the case the gate compares", async () => {
        // The gate lower-cases both sides. A routine stored upper-case must
        // survive an edit as something the gate still matches.
        list.mockResolvedValue({
            data: { routines: [routine({ needs_apps: ["GOOGLESHEETS", "GMAIL"] })] },
        });
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit Morning summary" }));
        fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));
        await waitFor(() => expect(update).toHaveBeenCalled());
        expect(update.mock.calls[0][0].body.needs_apps).toEqual(["googlesheets", "gmail"]);
    });

    it("offers the connected accounts, and not the ones nobody linked", async () => {
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: /Add a routine/ }));
        await waitFor(() => expect(screen.getByRole("button", { name: "Gmail" })).toBeTruthy());
        // Outlook is in the catalogue but unconnected: a routine that waits on
        // an account nobody linked would never run.
        expect(screen.queryByRole("button", { name: "Outlook" })).toBeNull();
    });

    it("sends the apps the person picked", async () => {
        create.mockResolvedValue({ data: routine() });
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: /Add a routine/ }));
        fireEvent.change(screen.getByLabelText("Call it"), { target: { value: "Evening sweep" } });
        fireEvent.click(await screen.findByRole("button", { name: "Gmail" }));
        fireEvent.click(screen.getByRole("button", { name: /Save it/ }));
        await waitFor(() => expect(create).toHaveBeenCalled());
        expect(create.mock.calls[0][0].body.needs_apps).toEqual(["gmail"]);
    });

    it("still shows an app the routine names after it was disconnected", async () => {
        // Hiding it would drop it on the next save -- the gate would quietly
        // stop firing and nothing on the screen would have said so.
        list.mockResolvedValue({ data: { routines: [routine({ needs_apps: ["outlook"] })] } });
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit Morning summary" }));
        const chip = await screen.findByRole("button", { name: /Outlook/ });
        expect(chip.textContent).toContain("not connected");
        expect(chip.getAttribute("aria-pressed")).toBe("true");
    });

    it("says so when nothing is connected, rather than showing an empty row", async () => {
        connectors.mockResolvedValue({ data: { available: false, connected_count: 0 } });
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: /Add a routine/ }));
        expect(await screen.findByText(/No accounts are connected yet/)).toBeTruthy();
    });

    it("keeps an offset the form never asked about", async () => {
        // -30 with a closing anchor is "half an hour before you shut". A zero
        // here moves the run and says nothing.
        list.mockResolvedValue({
            data: { routines: [routine({ anchor: "closing", offset_minutes: -30 })] },
        });
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit Morning summary" }));
        fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));
        await waitFor(() => expect(update).toHaveBeenCalled());
        expect(update.mock.calls[0][0].body.offset_minutes).toBe(-30);
    });

    it("creating still sends no offset, and no apps unless one was picked", async () => {
        create.mockResolvedValue({ data: routine() });
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: /Add a routine/ }));
        fireEvent.change(screen.getByLabelText("Call it"), { target: { value: "Evening sweep" } });
        fireEvent.click(screen.getByRole("button", { name: /Save it/ }));
        await waitFor(() => expect(create).toHaveBeenCalled());
        expect(create.mock.calls[0][0].body.offset_minutes).toBe(0);
        expect(create.mock.calls[0][0].body.needs_apps).toEqual([]);
    });

    it("cancelling an edit leaves the form empty for the next one", async () => {
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit Morning summary" }));
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        fireEvent.click(screen.getByRole("button", { name: /Add a routine/ }));
        expect((screen.getByLabelText("Call it") as HTMLInputElement).value).toBe("");
    });

    it("surfaces a refusal instead of closing the form on it", async () => {
        update.mockResolvedValue({ data: undefined, error: { detail: "Not allowed" } });
        render(<RoutinesPanel workflowId={7} />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit Morning summary" }));
        fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));
        expect(await screen.findByText("Not allowed")).toBeTruthy();
        expect((screen.getByLabelText("Call it") as HTMLInputElement).value).toBe(
            "Morning summary",
        );
    });
});
