import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InviteDecision } from "../InviteDecision";

const api = vi.hoisted(() => ({ preview: vi.fn(), confirm: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
    previewInviteDecisionApiV1PublicInviteRequestsDecideGet: api.preview,
    confirmInviteDecisionApiV1PublicInviteRequestsDecidePost: api.confirm,
}));

const request = {
    id: 7,
    name: "Asha Rao",
    email: "asha@clinic.in",
    note: "Chase unpaid invoices",
    state: "pending",
};

beforeEach(() => {
    api.preview.mockReset();
    api.confirm.mockReset();
});

describe("InviteDecision", () => {
    it("opening the link only reads; the decision waits for the button", async () => {
        api.preview.mockResolvedValue({ data: { action: "approve", request } });
        render(<InviteDecision token="tok-123456789" />);
        expect(await screen.findByText("Approve this invite request?")).toBeTruthy();
        expect(screen.getByText("Asha Rao")).toBeTruthy();
        expect(api.preview).toHaveBeenCalledWith({ query: { token: "tok-123456789" } });
        expect(api.confirm).not.toHaveBeenCalled();

        api.confirm.mockResolvedValue({
            data: { action: "approve", outcome: "approved", message: "Approved.", mail_sent: true, request: { ...request, state: "approved" } },
        });
        fireEvent.click(screen.getByTestId("invite-decision-confirm-button"));
        await waitFor(() => expect(api.confirm).toHaveBeenCalledWith({ body: { token: "tok-123456789" } }));
        expect(await screen.findByText("Approved")).toBeTruthy();
        expect(screen.getByText(/on their way to asha@clinic.in/)).toBeTruthy();
    });

    it("says who decided and when on a second visit, with no button", async () => {
        api.preview.mockResolvedValue({
            data: {
                action: "approve",
                request: { ...request, state: "approved", decided_message: "Already approved by founder@example.test on 8 Oct 2026 at 10:00 UTC." },
            },
        });
        render(<InviteDecision token="tok-123456789" />);
        expect(await screen.findByText(/Already approved by founder@example.test/)).toBeTruthy();
        expect(screen.queryByTestId("invite-decision-confirm-button")).toBeNull();
    });

    it("shows a refused link's reason", async () => {
        api.preview.mockResolvedValue({ error: { detail: "This link has expired." }, response: { status: 400 } });
        render(<InviteDecision token="tok-123456789" />);
        expect((await screen.findByTestId("invite-decision-error")).textContent).toBe("This link has expired.");
    });

    it("a link with no token asks for the email again and calls nothing", () => {
        render(<InviteDecision token={null} />);
        expect(screen.getByRole("alert").textContent).toMatch(/incomplete/);
        expect(api.preview).not.toHaveBeenCalled();
    });

    it("a reject link says nothing is sent", async () => {
        api.preview.mockResolvedValue({ data: { action: "reject", request } });
        api.confirm.mockResolvedValue({
            data: { action: "reject", outcome: "rejected", message: "Rejected.", mail_sent: false, request: { ...request, state: "rejected" } },
        });
        render(<InviteDecision token="tok-123456789" />);
        fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
        expect(await screen.findByText("Rejected")).toBeTruthy();
        expect(screen.getByText("Nothing was sent to them.")).toBeTruthy();
    });
});
