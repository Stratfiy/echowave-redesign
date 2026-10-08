/**
 * The approval dock above the composer and the exact approval screen
 * (screen 08). Both approve through the controls card with the version on
 * screen; neither claims more than the server says.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pending = vi.fn();
const preview = vi.fn();
const settle = vi.fn();
const revise = vi.fn();

vi.mock("@/client/sdk.gen", () => ({
    pendingApprovalsApiV1TodayApprovalsGet: (...a: unknown[]) => pending(...a),
    approvalPreviewApiV1TodayApprovalsEventIdGet: (...a: unknown[]) => preview(...a),
    settleActionApiV1TimelineActionsSettlePost: (...a: unknown[]) => settle(...a),
    reviseActionApiV1TimelineActionsRevisePost: (...a: unknown[]) => revise(...a),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import { ApprovalDetail } from "../ApprovalDetail";
import { ApprovalDock } from "../ApprovalDock";

const item = {
    id: 51,
    at: "2026-10-08T04:30:00Z",
    label: "Pay Acme Print's invoice",
    sentence: "Decibyl wants to: Pay Acme Print's invoice: ₹4,800",
    detail: "To accounts@acmeprint.example · ₹4,800 · From •••• 7890 · In razorpay",
    why: "The invoice is due today.",
    version: "abc123def4567890",
    state: "proposed",
    workflow_id: null,
    thread_id: null,
};

const full = {
    ...item,
    verb: item.label,
    action: "run_tool",
    account: "•••• 7890",
    recipient: "accounts@acmeprint.example",
    amount: "₹4,800",
    content: "Body: Invoice INV-2207 for October printing",
    attachments: ["INV-2207.pdf", "PO-118.pdf"],
    timing: "Runs 10 seconds after you approve; you can undo until then.",
    consequence: "Runs in razorpay and reaches people there. It cannot be undone.",
    reversible: false,
    expires_at: null,
    revisions: [],
    screen_state: "pending",
    error: null,
    done_note: null,
    fires_at: null,
    editable: true,
    bound_to_version: true,
    can_answer: true,
    answer_refusal: null,
    arguments: { to: "accounts@acmeprint.example", amount: "4800" },
};

describe("The approval dock", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        pending.mockResolvedValue({ data: { count: 3, items: [item] } });
        settle.mockResolvedValue({ data: {} });
    });

    it("says what Decibyl wants to do in one sentence, with one line of detail", async () => {
        render(<ApprovalDock />);
        expect((await screen.findByTestId("approval-dock-sentence")).textContent).toBe("Decibyl wants to: Pay Acme Print's invoice: ₹4,800");
        expect(screen.getByText(item.detail)).toBeTruthy();
        expect(screen.getByRole("button", { name: "Do it" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Don't" })).toBeTruthy();
        expect(screen.getByRole("link", { name: "2 more waiting" }).getAttribute("href")).toBe("/tasks");
    });

    it("Do it confirms the exact version shown", async () => {
        render(<ApprovalDock />);
        fireEvent.click(await screen.findByRole("button", { name: "Do it" }));
        await waitFor(() => expect(settle).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body).toEqual({ event_id: 51, verb: "confirm", version: "abc123def4567890" });
        expect(await screen.findByText(/Approved: Pay Acme Print's invoice/)).toBeTruthy();
        expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
    });

    it("Don't declines and sends no version", async () => {
        render(<ApprovalDock />);
        fireEvent.click(await screen.findByRole("button", { name: "Don't" }));
        await waitFor(() => expect(settle).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body).toEqual({ event_id: 51, verb: "decline" });
    });

    it("says why when the card changed since it was shown", async () => {
        settle.mockResolvedValue({ error: { detail: "This changed since you looked at it. Review the new version and confirm again." } });
        render(<ApprovalDock />);
        fireEvent.click(await screen.findByRole("button", { name: "Do it" }));
        expect(await screen.findByText(/This changed since you looked at it/)).toBeTruthy();
    });

    it("shows nothing when nothing is waiting", async () => {
        pending.mockResolvedValue({ data: { count: 0, items: [] } });
        const { container } = render(<ApprovalDock />);
        await waitFor(() => expect(pending).toHaveBeenCalled());
        expect(container.innerHTML).toBe("");
    });
});

describe("The exact approval screen", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        preview.mockResolvedValue({ data: full });
        settle.mockResolvedValue({ data: {} });
    });

    it("shows every field in full, with the account masked", async () => {
        render(<ApprovalDetail eventId={51} />);
        expect(await screen.findByRole("heading", { level: 2, name: "Pay Acme Print's invoice" })).toBeTruthy();
        expect(screen.getByText("accounts@acmeprint.example")).toBeTruthy();
        expect(screen.getByText("₹4,800")).toBeTruthy();
        expect(screen.getByText("•••• 7890")).toBeTruthy();
        expect(screen.getByText("INV-2207.pdf")).toBeTruthy();
        expect(screen.getByText("PO-118.pdf")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Approve and send" })).toBeTruthy();
    });

    it("approves the version on screen", async () => {
        render(<ApprovalDetail eventId={51} />);
        fireEvent.click(await screen.findByRole("button", { name: "Approve and send" }));
        await waitFor(() => expect(settle).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body).toMatchObject({ event_id: 51, verb: "confirm", version: "abc123def4567890" });
    });

    it("an unknown outcome offers Check delivery, never Retry", async () => {
        preview.mockResolvedValue({ data: { ...full, state: "outcome_unknown", screen_state: "outcome_unknown" } });
        render(<ApprovalDetail eventId={51} />);
        expect(await screen.findByRole("button", { name: "Check delivery" })).toBeTruthy();
        expect(screen.getByText(/Please do not send it again/)).toBeTruthy();
        expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
        expect(screen.queryByRole("button", { name: "Approve and send" })).toBeNull();
    });

    it("says Done only with the server's evidence", async () => {
        preview.mockResolvedValue({ data: { ...full, state: "done", screen_state: "completed", done_note: "Paid. Razorpay ref pay_123." } });
        render(<ApprovalDetail eventId={51} />);
        expect(await screen.findByText(/Paid\. Razorpay ref pay_123\./)).toBeTruthy();
    });

    it("a refused approval of an old version shows the new one to review", async () => {
        settle.mockResolvedValue({ error: { detail: "This changed since you looked at it. Review the new version and confirm again." } });
        preview.mockResolvedValueOnce({ data: full }).mockResolvedValue({ data: { ...full, version: "ffff000011112222", recipient: "new@acme.example" } });
        render(<ApprovalDetail eventId={51} />);
        fireEvent.click(await screen.findByRole("button", { name: "Approve and send" }));
        expect(await screen.findByText(/This is the new version/)).toBeTruthy();
        expect(screen.getByText("new@acme.example")).toBeTruthy();
    });

    it("someone else's card is read-only, with the reason and no Approve", async () => {
        preview.mockResolvedValue({ data: { ...full, can_answer: false, answer_refusal: "Only the person this is about can answer this card." } });
        render(<ApprovalDetail eventId={51} />);
        expect(await screen.findByText("Only the person this is about can answer this card.")).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Approve and send" })).toBeNull();
    });

    it("a desktop step handed to the computer says so, never Done", async () => {
        preview.mockResolvedValue({ data: { ...full, state: "released", screen_state: "executing" } });
        render(<ApprovalDetail eventId={51} />);
        expect(await screen.findByText("Handed to your computer. It takes this step once.")).toBeTruthy();
        expect(screen.queryByText(/^Done/)).toBeNull();
    });

    it("editing saves a new version through revise", async () => {
        revise.mockResolvedValue({ data: {} });
        render(<ApprovalDetail eventId={51} />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
        fireEvent.click(screen.getByRole("button", { name: "Save as a new version" }));
        await waitFor(() => expect(revise).toHaveBeenCalled());
        expect(revise.mock.calls[0][0].body).toEqual({ event_id: 51, arguments: { to: "accounts@acmeprint.example", amount: "4800" } });
    });
});
