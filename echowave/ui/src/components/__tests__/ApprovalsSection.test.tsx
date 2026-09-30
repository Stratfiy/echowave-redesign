/**
 * The approval matrix on the Settings page (KAN-160, slice 2). What these
 * hold: rules come from the server and are shown in their order; a member
 * sees a line, not a form; rupees typed become paise sent; a save sends the
 * whole list in order and nothing saves until Save; the CSV export asks the
 * server for text and hands the browser a file.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    rules: vi.fn(),
    save: vi.fn(),
    members: vi.fn(),
    audit: vi.fn(),
    csv: vi.fn(),
}));
vi.mock("@/client/sdk.gen", () => ({
    getApprovalRulesApiV1OrganizationsApprovalRulesGet: api.rules,
    saveApprovalRulesApiV1OrganizationsApprovalRulesPut: api.save,
    listMembersApiV1OrganizationsMembersGet: api.members,
    getAuditApiV1OrganizationsAuditGet: api.audit,
    getAuditCsvApiV1OrganizationsAuditCsvGet: api.csv,
}));
const roles = vi.hoisted(() => ({ admin: true }));
vi.mock("@/hooks/useAccessRoles", () => ({
    useAccessRoles: () => ({
        loaded: true,
        isOrganizationAdmin: roles.admin,
        isOrganizationOwner: roles.admin,
        organizationRole: roles.admin ? "owner" : "member",
        staffRole: null,
        isStaff: false,
    }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ApprovalsSection, toRule } from "../ApprovalsSection";

const RULES = {
    rules: [
        {
            id: 1,
            subject: "purchase_order",
            min_amount_paise: 10_000_000,
            max_amount_paise: null,
            approver_role: "owner",
            approver_user_id: null,
            position: 0,
        },
        {
            id: 2,
            subject: "card",
            min_amount_paise: null,
            max_amount_paise: null,
            approver_role: null,
            approver_user_id: 32,
            position: 1,
        },
    ],
};

beforeEach(() => {
    roles.admin = true;
    for (const fn of Object.values(api)) fn.mockReset();
    api.rules.mockResolvedValue({ data: RULES });
    api.save.mockResolvedValue({ data: RULES });
    api.members.mockResolvedValue({
        data: { members: [{ user_id: 32, email: "meera@x.com", role: "member" }] },
    });
    api.audit.mockResolvedValue({
        data: {
            entries: [
                {
                    id: 9,
                    at: "2026-09-28T04:00:00Z",
                    actor: "Nithish",
                    actor_user_id: 5,
                    action: "agent_live",
                    subject_kind: "agent",
                    subject_id: "3",
                    subject: "Meera",
                    before: null,
                    after: { is_live: false },
                    note: null,
                },
            ],
        },
    });
    api.csv.mockResolvedValue({ data: "at,actor\n2026,Nithish\n" });
});

describe("who approves what", () => {
    it("shows the rules in order, in rupees, with the member named", async () => {
        render(<ApprovalsSection />);
        const rows = await screen.findAllByTestId("rule-row");
        expect(rows).toHaveLength(2);
        expect((screen.getByLabelText("Rule 1 from") as HTMLInputElement).value).toBe("100000");
        expect((screen.getByLabelText("Rule 1 below") as HTMLInputElement).value).toBe("");
        expect(rows[0].textContent).toContain("Purchase order");
        expect(rows[0].textContent).toContain("The owner");
        expect(rows[1].textContent).toContain("meera@x.com");
        expect(api.audit).toHaveBeenCalledWith({ query: { limit: 20 } });
    });

    it("a member gets a line, not the form, and nothing is fetched", async () => {
        roles.admin = false;
        render(<ApprovalsSection />);
        expect(await screen.findByText(/Only an admin or the owner/)).toBeTruthy();
        expect(api.rules).not.toHaveBeenCalled();
    });

    it("nothing saves until Save, and Save sends the whole list in order", async () => {
        render(<ApprovalsSection />);
        await screen.findAllByTestId("rule-row");
        const save = screen.getByRole("button", { name: "Save" });
        expect((save as HTMLButtonElement).disabled).toBe(true);
        fireEvent.change(screen.getByLabelText("Rule 1 from"), { target: { value: "2,50,000" } });
        expect(api.save).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: "Move rule 2 up" }));
        fireEvent.click(save);
        await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1));
        const sent = api.save.mock.calls[0][0].body.rules;
        expect(sent.map((r: { subject: string }) => r.subject)).toEqual(["card", "purchase_order"]);
        expect(sent[1]).toMatchObject({ min_amount_paise: 25_000_000, approver_role: "owner", approver_user_id: null });
        expect(sent[0]).toMatchObject({ approver_role: null, approver_user_id: 32 });
    });

    it("Add rule, then Remove, leaves what was there", async () => {
        render(<ApprovalsSection />);
        await screen.findAllByTestId("rule-row");
        fireEvent.click(screen.getByRole("button", { name: /Add rule/ }));
        expect(screen.getAllByTestId("rule-row")).toHaveLength(3);
        fireEvent.click(screen.getByRole("button", { name: "Remove rule 3" }));
        expect(screen.getAllByTestId("rule-row")).toHaveLength(2);
    });

    it("lists the newest audit rows and exports the CSV the server sends", async () => {
        const url = vi.fn(() => "blob:audit");
        const revoke = vi.fn();
        Object.defineProperty(URL, "createObjectURL", { value: url, configurable: true });
        Object.defineProperty(URL, "revokeObjectURL", { value: revoke, configurable: true });
        const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
        render(<ApprovalsSection />);
        expect(await screen.findByText("agent live: Meera")).toBeTruthy();
        expect(screen.getByText("Nithish")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
        await waitFor(() => expect(api.csv).toHaveBeenCalledWith({ parseAs: "text" }));
        await waitFor(() => expect(click).toHaveBeenCalled());
        expect(url).toHaveBeenCalled();
        click.mockRestore();
    });
});

describe("a rule from the form", () => {
    it("turns rupees into paise and a member choice into a user id", () => {
        expect(toRule({ subject: "purchase_order", min: "1,00,000", max: "", approver: "owner" })).toEqual({
            subject: "purchase_order",
            min_amount_paise: 10_000_000,
            max_amount_paise: null,
            approver_role: "owner",
            approver_user_id: null,
        });
        expect(toRule({ subject: "*", min: "", max: "₹ 500.50", approver: "user:32" })).toEqual({
            subject: "*",
            min_amount_paise: null,
            max_amount_paise: 50_050,
            approver_role: null,
            approver_user_id: 32,
        });
    });
});
