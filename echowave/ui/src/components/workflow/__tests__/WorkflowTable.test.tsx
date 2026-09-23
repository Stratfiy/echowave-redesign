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
const outcomeBoard = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/client/sdk.gen", () => ({
    teamStatusApiV1TeamStatusGet: teamStatus,
    getOutcomeBoardApiV1WorkflowOutcomesGet: outcomeBoard,
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
    outcomeBoard.mockReset();
    outcomeBoard.mockResolvedValue({
        data: [
            {
                workflow_id: 18,
                name: "Narayani Dental front desk",
                outcomes: [
                    { code: "booked", label: "Booked", count: 6 },
                    { code: "callback", label: "Call back", count: 2 },
                    // Reliably the biggest bin on a quiet bot, and never the
                    // headline: a column led by it reports "mostly unclear"
                    // for every bot in the account.
                    { code: "unclear", label: "Unclear", count: 9 },
                ],
                runs: 12,
                classified: 11,
                truncated: false,
                configured: true,
            },
        ],
        error: undefined,
    });
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

describe("the agent list", () => {
    it("says what each agent has been doing, and names its last action", async () => {
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);

        expect(await screen.findByText("9 calls, 6 answered, 4 bookings")).toBeTruthy();
        expect(screen.getByText(/Booked an appointment · Cal\.com/)).toBeTruthy();
    });

    it("shows a dash for an agent the roster has nothing on, rather than an empty cell", async () => {
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);
        await screen.findByText("9 calls, 6 answered, 4 bookings");
        // Workflow-4597 is in the list but in neither response, so it gets a
        // dash in both the activity column and the judged one.
        expect(screen.getAllByText("—")).toHaveLength(2);
    });

    it("says what the classifier judged, worded apart from what was filed", async () => {
        // "4 bookings" is a row written to an outside system; "6 judged
        // booked" is what the model read the conversation to be. They
        // disagree constantly, and two numbers both called bookings would
        // read as one of them being wrong.
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);
        expect(await screen.findByText("6 judged booked")).toBeTruthy();
        expect(screen.getByText("9 calls, 6 answered, 4 bookings")).toBeTruthy();
    });

    it("never leads with unclear, however big that bin is", async () => {
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);
        await screen.findByText("6 judged booked");
        expect(screen.queryByText(/judged unclear/)).toBeNull();
    });

    it("puts the window in the heading, because the column beside it is a different one", async () => {
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);
        expect(await screen.findByText("Judged (30 days)")).toBeTruthy();
        expect(screen.getByText("Last 24 hours")).toBeTruthy();
    });

    it("shows the denominator, so a failing classifier does not read as an idle agent", async () => {
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);
        expect(await screen.findByText(/of 11 sorted/)).toBeTruthy();
    });

    it("marks an agent still on the default outcomes", async () => {
        outcomeBoard.mockResolvedValue({
            data: [
                {
                    workflow_id: 18,
                    name: "Narayani Dental front desk",
                    outcomes: [{ code: "booked", label: "Booked", count: 1 }],
                    runs: 3,
                    classified: 3,
                    truncated: false,
                    configured: false,
                },
            ],
            error: undefined,
        });
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);
        expect(await screen.findByText(/default outcomes/)).toBeTruthy();
    });

    it("says nothing landed when every sorted run went to unclear", async () => {
        outcomeBoard.mockResolvedValue({
            data: [
                {
                    workflow_id: 18,
                    name: "Narayani Dental front desk",
                    outcomes: [{ code: "unclear", label: "Unclear", count: 5 }],
                    runs: 5,
                    classified: 5,
                    truncated: false,
                    configured: true,
                },
            ],
            error: undefined,
        });
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);
        expect(await screen.findByText("Nothing landed")).toBeTruthy();
    });

    it("keeps the rows when the board request fails", async () => {
        // Losing a secondary count must not lose the agents.
        outcomeBoard.mockResolvedValue({ data: undefined, error: { detail: "nope" } });
        render(<WorkflowTable workflows={WORKFLOWS} showArchived={false} />);
        expect(await screen.findByText("Narayani Dental front desk")).toBeTruthy();
    });

    it("keeps every agent on screen when the roster request fails", async () => {
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
