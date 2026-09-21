import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { WorkflowEditorHeader } from "../WorkflowEditorHeader";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/ui/sidebar", () => ({ useSidebar: () => ({ toggleSidebar: vi.fn() }) }));
vi.mock("@/client/sdk.gen", () => ({ duplicateWorkflowEndpointApiV1WorkflowWorkflowIdDuplicatePost: vi.fn(), publishWorkflowApiV1WorkflowWorkflowIdPublishPost: vi.fn() }));
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
