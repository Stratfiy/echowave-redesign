import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ reply: vi.fn(), note: vi.fn(), update: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
    supportReplyApiV1AdminSupportTicketsTicketIdRepliesPost: api.reply,
    supportNoteApiV1AdminSupportTicketsTicketIdNotesPost: api.note,
    supportUpdateApiV1AdminSupportTicketsTicketIdPatch: api.update,
}));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: null, loading: false }) }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: () => ({ loading: false, orgFeatures: {} }) }));

import type { SupportCase } from "@/lib/support/staff";

import { SupportCaseThread } from "../SupportCaseThread";

const supportCase = {
    id: 5,
    subject: "Invoice never went out",
    category: "something_failed",
    category_label: "Something didn't work",
    status: "open",
    severity: "normal",
    assignee_user_id: null,
    requester_email: "asha@clinic.in",
    workspace_name: "Acme Clinic",
    organization_id: 3,
    requester_user_id: 11,
    created_at: null,
    updated_at: null,
    overdue: false,
    next_step: "Assign",
    linked_incident: null,
    version: 4,
    shared: { affected: null, sections: [], left_out: [], shared_at: null },
    messages: [{ id: 1, author_kind: "customer", body: "It never went.", created_at: null }],
    attachments: [],
    notes: [{ id: 2, author: "ravi@decibyl.ai", body: "Mail outage at 10:00", created_at: null }],
    history: [],
    requester: { id: 11, email: "asha@clinic.in" },
    workspace: { id: 3, name: "Acme Clinic" },
    first_response_at: null,
    resolved_at: null,
    reopened_count: 0,
    affected: null,
    diagnostics: { task: null, allowances: null },
} as unknown as SupportCase;

beforeEach(() => Object.values(api).forEach((fn) => fn.mockReset()));

describe("a support case", () => {
    it("keeps internal notes on their own surface, labelled staff-only", () => {
        render(<SupportCaseThread supportCase={supportCase} staff={[]} me={1} onChanged={vi.fn()} />);
        expect(screen.getByText(/only staff see these/)).toBeTruthy();
        expect(screen.getByTestId("internal-note").textContent).toContain("Mail outage");
        expect(screen.getByTestId("thread-customer").textContent).toContain("It never went.");
    });

    it("sends a note to the notes route, never as a reply", async () => {
        api.note.mockResolvedValue({ data: { id: 3 } });
        const onChanged = vi.fn();
        render(<SupportCaseThread supportCase={supportCase} staff={[]} me={1} onChanged={onChanged} />);
        fireEvent.click(screen.getByRole("tab", { name: "Internal note" }));
        const box = screen.getByPlaceholderText(/Only staff see this/);
        expect(box.getAttribute("data-mode")).toBe("note");
        fireEvent.change(box, { target: { value: "Checked the logs" } });
        fireEvent.click(screen.getByRole("button", { name: "Save note" }));
        await waitFor(() => expect(api.note).toHaveBeenCalledTimes(1));
        expect(api.reply).not.toHaveBeenCalled();
        expect(api.note.mock.calls[0][0].body.body).toBe("Checked the logs");
    });

    it("keeps an unsent reply and its key after a failure", async () => {
        api.reply.mockResolvedValueOnce({ error: { detail: "Timeout" }, response: { status: 504 } }).mockResolvedValueOnce({ data: { id: 9 } });
        render(<SupportCaseThread supportCase={supportCase} staff={[]} me={1} onChanged={vi.fn()} />);
        const box = screen.getByPlaceholderText("The customer reads this.");
        fireEvent.change(box, { target: { value: "Resent it." } });
        fireEvent.click(screen.getByRole("button", { name: "Send reply" }));
        expect(await screen.findByText("Timeout")).toBeTruthy();
        expect((box as HTMLTextAreaElement).value).toBe("Resent it.");
        fireEvent.click(screen.getByRole("button", { name: "Send again" }));
        await waitFor(() => expect(api.reply).toHaveBeenCalledTimes(2));
        expect(api.reply.mock.calls[0][0].body.client_key).toBe(api.reply.mock.calls[1][0].body.client_key);
        expect(api.reply.mock.calls[0][0].body.then_status).toBe("waiting_on_customer");
    });

    it("says who changed the case first instead of overwriting", async () => {
        api.update.mockResolvedValue({
            error: { detail: { message: "changed", current: { assignee_user_id: 8, status: "in_progress" } } },
            response: { status: 409 },
        });
        render(
            <SupportCaseThread
                supportCase={supportCase}
                staff={[
                    { id: 1, email: "me@decibyl.ai", role: "support" },
                    { id: 8, email: "priya@decibyl.ai", role: "support" },
                ]}
                me={1}
                onChanged={vi.fn()}
            />,
        );
        fireEvent.change(screen.getByLabelText("Assignee"), { target: { value: "1" } });
        expect(await screen.findByText(/Someone changed this case first. It is now In progress, assigned to priya@decibyl.ai./)).toBeTruthy();
        expect(api.update.mock.calls[0][0].body).toEqual({ expected_version: 4, assignee_user_id: 1 });
    });
});
