import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ActionPreview, type ActionPreviewData } from "../ActionPreview";

const preview: ActionPreviewData = {
    id: "p-1",
    version: 3,
    action: "Send WhatsApp message",
    account: "Clinic WhatsApp",
    recipient: "Ravi Kumar",
    content: "Hi Ravi, the proposal is attached.",
    attachments: ["Proposal.pdf"],
    timing: "Now",
    expiresAt: "2026-10-07T12:00:00Z",
    consequence: "Ravi will receive this on WhatsApp.",
};
const before = new Date("2026-10-07T11:00:00Z").getTime();

describe("ActionPreview", () => {
    it("shows the exact action and approves this id and version", () => {
        const onApprove = vi.fn();
        render(<ActionPreview preview={preview} status="pending" onApprove={onApprove} now={before} />);
        expect(screen.getByText("Ravi Kumar")).toBeTruthy();
        expect(screen.getByText("Hi Ravi, the proposal is attached.")).toBeTruthy();
        expect(screen.getByText("Proposal.pdf")).toBeTruthy();
        expect(screen.getByText("v3")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Approve" }));
        expect(onApprove).toHaveBeenCalledWith("p-1", 3);
    });

    it("cannot be approved twice while the approval is committing", () => {
        const onApprove = vi.fn();
        render(<ActionPreview preview={preview} status="committing" onApprove={onApprove} now={before} />);
        const button = screen.getByRole("button", { name: /Approving/ }) as HTMLButtonElement;
        expect(button.disabled).toBe(true);
        fireEvent.click(button);
        expect(onApprove).not.toHaveBeenCalled();
    });

    it("refuses an expired or changed preview and asks for a fresh review", () => {
        const onApprove = vi.fn();
        const later = new Date("2026-10-07T13:00:00Z").getTime();
        const { rerender } = render(<ActionPreview preview={preview} status="pending" onApprove={onApprove} now={later} />);
        expect(screen.getByText(/approval expired/)).toBeTruthy();
        expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(true);
        rerender(<ActionPreview preview={preview} status="changed" onApprove={onApprove} now={before} />);
        expect(screen.getByText(/changed since you saw it/)).toBeTruthy();
        expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("says what happened once settled and offers nothing more", () => {
        render(<ActionPreview preview={preview} status="approved" onApprove={vi.fn()} now={before} />);
        expect(screen.getByText(/It runs once/)).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    });

    it("hands Edit and Cancel back to the caller", () => {
        const onEdit = vi.fn();
        const onCancel = vi.fn();
        render(<ActionPreview preview={preview} status="pending" onApprove={vi.fn()} onEdit={onEdit} onCancel={onCancel} now={before} />);
        fireEvent.click(screen.getByRole("button", { name: "Edit" }));
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        expect(onEdit).toHaveBeenCalledOnce();
        expect(onCancel).toHaveBeenCalledOnce();
    });
});
