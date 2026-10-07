import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ commands: vi.fn(), preview: vi.fn(), request: vi.fn() }));
const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
    supportCommandsApiV1AdminSupportActionsCommandsGet: api.commands,
    supportActionPreviewApiV1AdminSupportActionsPreviewPost: api.preview,
    supportActionRequestApiV1AdminSupportActionsPost: api.request,
}));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: null, loading: false }) }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: () => ({ loading: false, orgFeatures: {} }) }));

import { ActionComposer } from "../ActionComposer";

const commands = [
    {
        kind: "pause_routine",
        title: "Pause a routine",
        description: "Stop a routine.",
        approver_role: "support",
        needs_customer_request: true,
        state: "available",
        reason: null,
        fields: [{ name: "routine_id", label: "Routine id", type: "integer", min: 1 }],
    },
    {
        kind: "refund_payment",
        title: "Refund a payment",
        description: "Finance only.",
        approver_role: "superadmin",
        needs_customer_request: false,
        state: "unavailable",
        reason: "Needs a finance role.",
        fields: [],
    },
];

const shown = {
    title: "Pause the routine “Morning brief”",
    changes: [{ field: "Routine #9", old: "on", new: "paused" }],
    impact: "It stops.",
    dependencies: [],
    customer_summary: "Pause",
    target: { organization_id: 3, user_id: 11, ticket_id: 5 },
    environment: "local",
    approvers: "A second staff member",
    version: "v1",
};

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    router.push.mockReset();
    api.commands.mockResolvedValue({ data: { commands } });
    api.preview.mockResolvedValue({ data: shown });
});

describe("requesting a support action", () => {
    it("lists commands that cannot run, with the reason, and cannot pick them", async () => {
        render(<ActionComposer organizationId={3} targetUserId={11} ticketId={5} />);
        const refund = await screen.findByTestId("command-refund_payment");
        expect(refund.getAttribute("data-state")).toBe("unavailable");
        expect(refund.textContent).toContain("Needs a finance role.");
        expect((refund.querySelector("input") as HTMLInputElement).disabled).toBe(true);
    });

    it("requests exactly the previewed version, and a field change clears the preview", async () => {
        api.request.mockResolvedValue({ data: { action: { id: 21 }, created: true } });
        render(<ActionComposer organizationId={3} targetUserId={11} ticketId={5} />);
        fireEvent.click(await screen.findByLabelText(/Pause a routine/));
        fireEvent.change(screen.getByLabelText("Routine id"), { target: { value: "9" } });
        fireEvent.click(screen.getByRole("button", { name: "Preview" }));
        expect(await screen.findByTestId("support-action-preview")).toBeTruthy();
        expect(api.preview.mock.calls[0][0].body).toEqual({
            kind: "pause_routine",
            organization_id: 3,
            target_user_id: 11,
            ticket_id: 5,
            params: { routine_id: 9 },
        });
        fireEvent.change(screen.getByLabelText("Routine id"), { target: { value: "10" } });
        expect(screen.queryByTestId("support-action-preview")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Preview" }));
        await screen.findByTestId("support-action-preview");
        fireEvent.change(screen.getByLabelText("Reason (recorded in the audit)"), { target: { value: "Customer asked to stop it." } });
        fireEvent.click(screen.getByRole("button", { name: "Request approval" }));
        await waitFor(() => expect(router.push).toHaveBeenCalledWith("/superadmin/support/actions/21"));
        expect(api.request.mock.calls[0][0].body).toMatchObject({ expected_version: "v1", reason: "Customer asked to stop it.", params: { routine_id: 10 } });
        expect(api.request.mock.calls[0][0].headers["Idempotency-Key"]).toBeTruthy();
    });
});
