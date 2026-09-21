import { fireEvent, render, screen } from "@testing-library/react";
import type { NodeProps } from "@xyflow/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useWorkflowStore } from "@/app/workflow/[workflowId]/stores/workflowStore";
import { FlowNodeData } from "@/components/flow/types";

import { GenericNode } from "../nodes/GenericNode";

const state = vi.hoisted(() => ({ save: vi.fn(), readOnly: false, form: vi.fn(), specs: new Map() }));
vi.mock("@xyflow/react", () => ({ Position: { Left: "left", Right: "right", Top: "top" }, NodeToolbar: ({ children, isVisible }: { children: React.ReactNode; isVisible: boolean }) => isVisible ? <div>{children}</div> : null }));
vi.mock("../nodes/BaseHandle", () => ({ BaseHandle: (props: { type: string; position: string }) => <span data-testid={`${props.type}-port`} data-position={props.position} /> }));
vi.mock("@/app/workflow/[workflowId]/contexts/WorkflowContext", () => ({ useWorkflow: () => ({ saveWorkflow: state.save, readOnly: state.readOnly, tools: [], documents: [], recordings: [] }) }));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: null }) }));
vi.mock("@/components/flow/renderer", () => ({ useNodeSpecs: () => ({ bySpecName: state.specs }), NodeEditForm: (props: { values: Record<string, unknown> }) => { state.form(props); return <div>Inspector fields</div>; } }));
vi.mock("../nodes/common/NodeEditDialog", () => ({ NodeEditDialog: ({ children, open }: { children: React.ReactNode; open: boolean }) => open ? <div role="dialog">{children}</div> : null }));
const data: FlowNodeData = { name: "Support", prompt: "A very long instruction that belongs only in the inspector", tool_uuids: ["tool"], document_uuids: ["document"] };
function node(type = "agentNode", extra: Partial<FlowNodeData> = {}) { return <GenericNode {...({ id: "a", type, selected: true, data: { ...data, ...extra } } as NodeProps & { type: string; data: FlowNodeData })} />; }
beforeEach(() => {
    state.save.mockReset(); state.form.mockReset(); state.readOnly = false;
    state.specs = new Map(["agentNode", "startCall", "trigger"].map((name) => [name, { name, display_name: name === "agentNode" ? "Agent" : name, icon: "Bot", properties: [{ name: "name" }, { name: "prompt" }, { name: "tool_uuids", type: "tool_refs" }, { name: "document_uuids", type: "document_refs" }] }]));
    useWorkflowStore.getState().initializeWorkflow(39, "Assistant", [{ id: "a", type: "trigger", position: { x: 0, y: 0 }, data }], []);
});
describe("compact canvas nodes", () => {
    it("shows identity and ports without prompt or reference lists", () => {
        render(node());
        expect(screen.getByText("Support")).toBeTruthy();
        expect(screen.getByText("Agent")).toBeTruthy();
        expect(screen.queryByText(data.prompt!)).toBeNull();
        expect(screen.queryByText("Tools:")).toBeNull();
        expect(screen.queryByText("Documents:")).toBeNull();
        expect(screen.getByTestId("target-port").getAttribute("data-position")).toBe("left");
        expect(screen.getByTestId("source-port").getAttribute("data-position")).toBe("right");
    });
    it("opens the full inspector with original values from the keyboard", () => {
        render(node());
        fireEvent.keyDown(screen.getByLabelText(/Press Enter to inspect/), { key: "Enter" });
        expect(screen.getByRole("dialog")).toBeTruthy();
        const values = state.form.mock.lastCall?.[0].values;
        expect(values.prompt).toBe(data.prompt);
        expect(values.tool_uuids).toEqual(["tool"]);
        expect(values.document_uuids).toEqual(["document"]);
    });
    it("never generates trigger paths or saves simply by rendering", () => {
        const before = useWorkflowStore.getState().nodes;
        render(node("trigger"));
        expect(useWorkflowStore.getState().nodes).toBe(before);
        expect(useWorkflowStore.getState().isDirty).toBe(false);
        expect(state.save).not.toHaveBeenCalled();
    });
    it("exposes validation and runtime state without using only colour", () => {
        const { rerender } = render(node("agentNode", { invalid: true }));
        expect(screen.getByLabelText("Needs attention")).toBeTruthy();
        rerender(node("agentNode", { runtime_active: true }));
        expect(screen.getByLabelText("Running")).toBeTruthy();
    });
    it("hides destructive controls for historical versions", () => {
        state.readOnly = true;
        render(node());
        expect(screen.queryByRole("button", { name: "Delete Support" })).toBeNull();
    });
    it("does not label a voice step Off because an optional feature is disabled", () => {
        state.specs.get("agentNode").category = "call_node";
        state.specs.get("agentNode").properties.push({ name: "interruption_enabled", default: false });
        render(node("agentNode", { interruption_enabled: false }));
        expect(screen.queryByText("Off")).toBeNull();
    });
});
