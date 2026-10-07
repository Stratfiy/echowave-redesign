import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    load: vi.fn(),
    me: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    withdraw: vi.fn(),
    run: vi.fn(),
    reconcile: vi.fn(),
}));
vi.mock("@/client/sdk.gen", () => ({
    supportActionApiV1AdminSupportActionsActionIdGet: api.load,
    getAuthUserApiV1UserAuthUserGet: api.me,
    supportActionApproveApiV1AdminSupportActionsActionIdApprovePost: api.approve,
    supportActionRejectApiV1AdminSupportActionsActionIdRejectPost: api.reject,
    supportActionWithdrawApiV1AdminSupportActionsActionIdWithdrawPost: api.withdraw,
    supportActionRunApiV1AdminSupportActionsActionIdRunPost: api.run,
    supportActionReconcileApiV1AdminSupportActionsActionIdReconcilePost: api.reconcile,
}));

import { ActionDetail } from "../ActionDetail";

function action(overrides: Record<string, unknown> = {}) {
    return {
        id: 4,
        ticket_id: 5,
        organization_id: 3,
        target_user_id: 11,
        kind: "grant_usage",
        title: "Grant temporary usage",
        params: { allowance: "model_turns", extra: 20, days: 3 },
        preview: {
            title: "Add 20 messages a day for 3 days",
            changes: [{ field: "Daily messages", old: 50, new: 70 }],
            impact: "Up to 60 more messages in total.",
            dependencies: ["Daily limits are on."],
            customer_summary: "Extra messages",
            target: { organization_id: 3, user_id: 11, ticket_id: 5 },
            environment: "production",
            approvers: "A second staff member who is a superadmin",
        },
        version: "a".repeat(32),
        reason: "Customer asked.",
        state: "requested",
        environment: "production",
        approver_role: "superadmin",
        requested_by: "ravi@decibyl.ai",
        requested_by_id: 1,
        approved_by: null,
        approved_by_id: null,
        approved_version: null,
        approved_at: null,
        decided_note: null,
        run_by: null,
        queued_at: null,
        started_at: null,
        finished_at: null,
        result: null,
        idempotency_key: "k-1",
        expires_at: null,
        created_at: null,
        notice: null,
        history: [],
        ...overrides,
    };
}

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
});

describe("a support action", () => {
    it("shows old and new values, production, and that the requester cannot approve", async () => {
        api.load.mockResolvedValue({ data: action() });
        api.me.mockResolvedValue({ data: { id: 1, staff_role: "superadmin" } });
        render(<ActionDetail actionId={4} />);
        expect(await screen.findByText("Add 20 messages a day for 3 days")).toBeTruthy();
        expect(screen.getByText("70")).toBeTruthy();
        expect(screen.getAllByText("production").length).toBeGreaterThan(0);
        expect(await screen.findByText(/You asked for it, so you cannot/)).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Approve this version" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    });

    it("needs the right tier to approve", async () => {
        api.load.mockResolvedValue({ data: action() });
        api.me.mockResolvedValue({ data: { id: 2, staff_role: "support" } });
        render(<ActionDetail actionId={4} />);
        expect(await screen.findByText("Approving this needs the superadmin role.")).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Approve this version" })).toBeNull();
    });

    it("approves the exact version it shows", async () => {
        api.load.mockResolvedValue({ data: action() });
        api.me.mockResolvedValue({ data: { id: 2, staff_role: "superadmin" } });
        api.approve.mockResolvedValue({ data: action({ state: "approved" }) });
        render(<ActionDetail actionId={4} />);
        fireEvent.click(await screen.findByRole("button", { name: "Approve this version" }));
        await waitFor(() => expect(api.approve).toHaveBeenCalledTimes(1));
        expect(api.approve.mock.calls[0][0].body).toEqual({ version: "a".repeat(32) });
    });

    it("runs once, and reads queued as accepted, not done", async () => {
        api.load.mockResolvedValueOnce({ data: action({ state: "approved", approved_by: "boss@decibyl.ai", approved_by_id: 2 }) });
        api.load.mockResolvedValue({ data: action({ state: "queued" }) });
        api.me.mockResolvedValue({ data: { id: 1, staff_role: "support" } });
        api.run.mockResolvedValue({ data: { action: action({ state: "queued" }), queued: true } });
        render(<ActionDetail actionId={4} />);
        fireEvent.click(await screen.findByRole("button", { name: "Run" }));
        await waitFor(() => expect(screen.getByTestId("action-state").textContent).toContain("accepted, not finished"));
        expect(api.run).toHaveBeenCalledTimes(1);
        expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
        expect((screen.getByRole("button", { name: /Running/ }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("offers reconciliation, never a rerun, when the outcome is unknown", async () => {
        api.load.mockResolvedValue({
            data: action({ state: "outcome_unknown", notice: "We are checking whether this happened.", approved_by_id: 2 }),
        });
        api.me.mockResolvedValue({ data: { id: 1, staff_role: "support" } });
        render(<ActionDetail actionId={4} />);
        expect(await screen.findByText("We are checking whether this happened.")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Check what happened" })).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    });
});
