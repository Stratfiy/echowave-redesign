/**
 * The bot list shows what each bot has been doing.
 *
 * This screen used to lead with a database id and a creation date — two facts
 * an owner has no use for — while the sentence that answers "is it working?"
 * already existed on the server and was rendered only on the home screen. The
 * tests below are about that sentence appearing, not about the id being gone:
 * a column that silently stops rendering is the failure worth catching.
 */

import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const teamStatus = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/client/sdk.gen", () => ({
    teamStatusApiV1TeamStatusGet: teamStatus,
    moveWorkflowToFolderApiV1WorkflowWorkflowIdFolderPut: vi.fn(),
    updateWorkflowLiveApiV1WorkflowWorkflowIdLivePut: vi.fn(),
    updateWorkflowStatusApiV1WorkflowWorkflowIdStatusPut: vi.fn(),
}));

import { WorkflowTable } from "../WorkflowTable";

const WORKFLOWS = [
    { id: 18, name: "Narayani Dental front desk", status: "active", is_live: true, created_at: "2026-09-10T00:00:00Z", total_runs: 110 },
    { id: 15, name: "Workflow-4597", status: "active", is_live: true, created_at: "2026-09-10T00:00:00Z", total_runs: 0 },
];

beforeEach(() => {
    teamStatus.mockReset();
    teamStatus.mockResolvedValue({
        data: {
            hours: 24,
            members: [
                {
                    workflow_id: 18,
                    name: "Narayani Dental front desk",
                    is_live: true,
                    status: "9 calls, 6 answered, 4 bookings",
                    tone: "working",
                    at: "2026-09-13T10:00:00Z",
                    calls: 9,
                    answered: 6,
                    outcomes: 4,
                    failures: 0,
                    last_action: { label: "Booked an appointment", app: "Cal.com", status: "ok", at: "2026-09-13T10:00:00Z" },
                },
            ],
        },
    });
});

describe("the bot list", () => {
    it("says what each bot has been doing, and names its last action", async () => {
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);

        expect(await screen.findByText("9 calls, 6 answered, 4 bookings")).toBeTruthy();
        expect(screen.getByText(/Booked an appointment · Cal\.com/)).toBeTruthy();
    });

    it("shows a dash for a bot the roster has nothing on, rather than an empty cell", async () => {
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);
        await screen.findByText("9 calls, 6 answered, 4 bookings");
        // Workflow-4597 is in the list but not in the roster response.
        expect(screen.getByText("—")).toBeTruthy();
    });

    it("keeps every bot on screen when the roster request fails", async () => {
        teamStatus.mockRejectedValue(new Error("network"));
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);

        // The point: losing the sentence must not lose the agents.
        expect(screen.getByText("Narayani Dental front desk")).toBeTruthy();
        expect(screen.getByText("Workflow-4597")).toBeTruthy();
        await waitFor(() => expect(teamStatus).toHaveBeenCalled());
        expect(screen.getByText("Narayani Dental front desk")).toBeTruthy();
    });

    it("does not ask for a 24-hour roster on the archived list", () => {
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={true} />);
        expect(teamStatus).not.toHaveBeenCalled();
    });

    it("no longer leads with the database id", async () => {
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);
        await screen.findByText("9 calls, 6 answered, 4 bookings");
        expect(screen.queryByRole("columnheader", { name: "ID" })).toBeNull();
        expect(screen.queryByRole("columnheader", { name: /created at/i })).toBeNull();
        expect(screen.getByRole("columnheader", { name: /last 24 hours/i })).toBeTruthy();
    });
});
