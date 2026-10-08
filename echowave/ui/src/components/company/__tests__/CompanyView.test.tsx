import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CompanyData } from "../useCompany";

const state: { data: CompanyData | null; dismiss: ReturnType<typeof vi.fn> } = { data: null, dismiss: vi.fn() };
const push = vi.fn();
const post = vi.fn();

vi.mock("../useCompany", () => ({
    useCompany: () => ({ data: state.data, error: null, dismissIncident: state.dismiss }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/client/sdk.gen", () => ({
    postMessageApiV1TimelineMessagePost: (...args: unknown[]) => post(...args),
}));

import { CompanyView } from "../CompanyView";

const member = (id: number, name: string, tone: string) => ({
    workflow_id: id,
    workflow_uuid: null,
    name,
    is_live: true,
    status: `${name} is on it`,
    tone,
    at: null,
    calls: 0,
    answered: 0,
    outcomes: 2,
    failures: 0,
    last_action: null,
});

beforeEach(() => {
    push.mockReset();
    post.mockReset();
    state.dismiss = vi.fn();
    state.data = {
        members: [member(1, "Ava", "working"), member(2, "Ben", "attention")],
        workflows: [
            { id: 1, name: "Ava", status: "active", created_at: "", total_runs: 0, folder_id: 7, handle: "ava" },
            { id: 2, name: "Ben", status: "active", created_at: "", total_runs: 0, folder_id: null },
        ],
        folders: [{ id: 7, name: "Sales", created_at: "" }],
        tasks: [],
        policies: [],
        incidents: [
            { id: 9, policy_id: 1, workflow_id: 1, threshold: "hard", window_start: "", window_end: null, limit_credits: 100, observed_credits: 100, status: "open", created_at: null },
        ],
        routines: [],
        events: [],
    };
});

describe("CompanyView", () => {
    it("shows a spinner until the data is in", () => {
        state.data = null;
        render(<CompanyView />);
        expect(screen.getByLabelText("Loading")).toBeTruthy();
    });

    it("draws the org chart by team, and what needs you", () => {
        render(<CompanyView />);
        const chart = screen.getByRole("region", { name: "Org chart" });
        expect(within(chart).getByText("Sales")).toBeTruthy();
        expect(within(chart).getByText("No team yet")).toBeTruthy();
        expect(within(chart).getByRole("link", { name: /Ava/ }).getAttribute("href")).toBe("/workflow/1/thread");

        const needs = screen.getByRole("region", { name: "Needs you" });
        expect(within(needs).getByText("Ava hit its budget")).toBeTruthy();
        expect(within(needs).getByText("Ben needs you")).toBeTruthy();
    });

    it("dismisses a budget alert", () => {
        render(<CompanyView />);
        fireEvent.click(screen.getByRole("button", { name: "Dismiss: Ava hit its budget" }));
        expect(state.dismiss).toHaveBeenCalledWith(9);
    });

    it("sends a question to Decibyl and opens the thread", async () => {
        post.mockResolvedValue({ data: {} });
        render(<CompanyView />);
        fireEvent.click(screen.getByRole("button", { name: "Who needs me today?" }));
        await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/overview"));
        expect(post).toHaveBeenCalledWith({ body: { assistant: true, text: "Who needs me today?" } });
    });
});
