import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { WorkflowEditorHeader } from "../WorkflowEditorHeader";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/ui/sidebar", () => ({ useSidebar: () => ({ toggleSidebar: vi.fn() }) }));
const setVisibility = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({ duplicateWorkflowEndpointApiV1WorkflowWorkflowIdDuplicatePost: vi.fn(), publishWorkflowApiV1WorkflowWorkflowIdPublishPost: vi.fn(), updateWorkflowVisibilityApiV1WorkflowWorkflowIdVisibilityPut: (...a: unknown[]) => setVisibility(...a) }));
// Workspace roles (MP-2) are asked about over the network; off here, and
// their menu item is tested at the bottom of this file.
const roles = vi.hoisted(() => ({ available: false }));
vi.mock("@/lib/features", () => ({ useFeature: () => roles.available }));
vi.mock("@/components/workflow/SaveAsRoleDialog", () => ({ SaveAsRoleDialog: ({ open }: { open: boolean }) => (open ? <div>Save as a workspace role</div> : null) }));
const props = () => ({ workflowName: "Clinic front desk and appointments", isDirty: false, workflowValidationErrors: [], rfInstance: { current: null }, workflowId: 39, saveWorkflow: vi.fn().mockResolvedValue(undefined), user: { id: "u" }, onPhoneCallClick: vi.fn(), onTestAgentClick: vi.fn(), onHistoryClick: vi.fn(), activeVersionLabel: "v2 (Draft)", isViewingHistoricalVersion: false, onBackToDraft: vi.fn(), hasDraft: true, onPublished: vi.fn(), renameWorkflow: vi.fn().mockResolvedValue(undefined) });
function openMore() { fireEvent.pointerDown(screen.getByRole("button", { name: "More agent actions" }), { button: 0, ctrlKey: false, pointerType: "mouse" }); }
describe("responsive editor actions", () => {
    it("keeps the complete name and an accessible history control", () => {
        const p = props(); render(<WorkflowEditorHeader {...p} />);
        expect(screen.getByRole("heading", { name: p.workflowName }).textContent).toBe(p.workflowName);
        fireEvent.click(screen.getByRole("button", { name: "Version history: v2 (Draft)" }));
        expect(p.onHistoryClick).toHaveBeenCalledOnce();
        fireEvent.click(screen.getByRole("button", { name: "Test" }));
        expect(p.onTestAgentClick).toHaveBeenCalledOnce();
        expect(screen.getByRole("button", { name: "Publish" })).toBeTruthy();
    });
    it("moves phone calls into More while preserving the callback", () => {
        const p = props(); render(<WorkflowEditorHeader {...p} />);
        expect(screen.queryByRole("button", { name: "Phone Call" })).toBeNull();
        openMore(); fireEvent.click(screen.getByRole("menuitem", { name: "Phone Call" }));
        expect(p.onPhoneCallClick).toHaveBeenCalledOnce();
    });
    it("retains the unsaved phone and publish guard and reachable Save", () => {
        render(<WorkflowEditorHeader {...props()} isDirty />);
        expect(screen.getByRole("button", { name: "Publish" }).hasAttribute("disabled")).toBe(true);
        expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(false);
        openMore();
        expect(screen.getByRole("menuitem", { name: "Phone Call" }).getAttribute("aria-disabled")).toBe("true");
    });
    it("keeps historical versions read-only and offers return to draft", () => {
        const p = props(); render(<WorkflowEditorHeader {...p} isViewingHistoricalVersion />);
        expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Publish" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Back to Draft" }));
        expect(p.onBackToDraft).toHaveBeenCalledOnce();
        openMore(); expect(screen.queryByRole("menuitem", { name: "Phone Call" })).toBeNull();
    });
});

describe("saving an agent as a workspace role (MP-2)", () => {
    it("is offered only while workspace roles are on, and opens the save dialog", async () => {
        roles.available = false;
        const { unmount } = render(<WorkflowEditorHeader {...props()} />);
        openMore();
        expect(screen.queryByText("Save as workspace role")).toBeNull();
        unmount();

        roles.available = true;
        render(<WorkflowEditorHeader {...props()} />);
        openMore();
        fireEvent.click(await screen.findByText("Save as workspace role"));
        expect(await screen.findByText("Save as a workspace role")).toBeTruthy();
        roles.available = false;
    });

    it("is not offered on a historical version", () => {
        roles.available = true;
        render(<WorkflowEditorHeader {...props()} isViewingHistoricalVersion />);
        openMore();
        expect(screen.queryByText("Save as workspace role")).toBeNull();
        roles.available = false;
    });
});


describe("who may see the agent (KAN-158)", () => {
    it("is not offered while workspace roles are off", () => {
        roles.available = false;
        render(<WorkflowEditorHeader {...props()} />);
        openMore();
        expect(screen.queryByRole("menuitem", { name: /Visible to/ })).toBeNull();
    });
    it("hides from members on the flat visibility route and says so", async () => {
        roles.available = true;
        setVisibility.mockResolvedValue({ data: { id: 39, visibility: "admins" } });
        render(<WorkflowEditorHeader {...props()} visibility="everyone" />);
        openMore();
        fireEvent.click(screen.getByRole("menuitem", { name: /Visible to everyone/ }));
        await vi.waitFor(() =>
            expect(setVisibility).toHaveBeenCalledWith({ path: { workflow_id: 39 }, body: { visibility: "admins" } }),
        );
        openMore();
        expect(screen.getByRole("menuitem", { name: /Visible to admins only/ })).toBeTruthy();
        roles.available = false;
    });
});
