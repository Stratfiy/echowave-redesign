import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useWorkflowStore } from "@/app/workflow/[workflowId]/stores/workflowStore";
import { FlowEdge, FlowNode } from "@/components/flow/types";

import { GraphEditChat } from "../GraphEditChat";

const mocks = vi.hoisted(() => ({ post: vi.fn(), auth: { user: { id: "u" } as { id: string } | null, loading: false } }));
vi.mock("@/client/client.gen", () => ({ client: { post: mocks.post } }));
vi.mock("@/lib/auth", () => ({ useAuth: () => mocks.auth }));
const node = (prompt: string) => ({ id: "agent", type: "agent", position: { x: 0, y: 0 }, data: { prompt } }) as FlowNode;
const reply = { status: "proposal", summary: "Make the greeting warm", graph: { nodes: [node("Warm")], edges: [] }, changes: [{ node_id: "agent", field: "prompt", before: "Old", after: "Warm" }] };

describe("GraphEditChat", () => {
    beforeEach(() => {
        mocks.post.mockReset();
        mocks.auth = { user: { id: "u" }, loading: false };
        useWorkflowStore.setState({ workflowId: 39, nodes: [node("Old")], edges: [], isDirty: false, history: [], historyIndex: -1 });
    });
    async function request() {
        fireEvent.change(screen.getByLabelText("What should change?"), { target: { value: "Warm greeting" } });
        fireEvent.click(screen.getByRole("button", { name: "Propose edit" }));
        await screen.findByRole("button", { name: "Apply to draft" });
    }
    it("proposes against unsaved nodes and applies only on explicit review", async () => {
        mocks.post.mockResolvedValue({ data: reply });
        render(<GraphEditChat workflowId={39} />);
        await request();
        expect(mocks.post).toHaveBeenCalledWith(expect.objectContaining({ body: { workflow_id: 39, message: "Warm greeting", graph: { nodes: [node("Old")], edges: [] } } }));
        expect(useWorkflowStore.getState().nodes[0].data.prompt).toBe("Old");
        fireEvent.click(screen.getByRole("button", { name: "Apply to draft" }));
        expect(useWorkflowStore.getState().nodes[0].data.prompt).toBe("Warm");
        expect(useWorkflowStore.getState().isDirty).toBe(true);
        expect(mocks.post).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole("button", { name: "Undo" }));
        expect(useWorkflowStore.getState().nodes[0].data.prompt).toBe("Old");
        fireEvent.click(screen.getByRole("button", { name: "Redo" }));
        expect(useWorkflowStore.getState().nodes[0].data.prompt).toBe("Warm");
    });
    it("rejects a proposal after a graph edit", async () => {
        mocks.post.mockResolvedValue({ data: reply });
        render(<GraphEditChat workflowId={39} />);
        await request();
        act(() => useWorkflowStore.setState({ nodes: [node("Manual edit")] }));
        expect((screen.getByRole("button", { name: "Apply to draft" }) as HTMLButtonElement).disabled).toBe(true);
        expect(screen.getByText(/Your draft changed since/)).toBeTruthy();
    });
    it("reviews added steps and connections and restores exact topology with Undo", async () => {
        const original = [node("Old")];
        const added = { ...node("Ask for the date"), id: "date", data: { name: "Appointment date", prompt: "Ask for the date" } } as FlowNode;
        const edge = { id: "to-date", source: "agent", target: "date", data: { label: "Continue", condition: "ready" } } as FlowEdge;
        mocks.post.mockResolvedValue({ data: {
            status: "proposal", summary: "Ask for the date first",
            graph: { nodes: [...original, added], edges: [edge] },
            changes: [
                { node_id: "date", field: "node_added", before: null, after: "Ask for the date" },
                { node_id: "date", field: "edge_added", before: null, after: "Agent → Appointment date" },
            ],
        } });
        render(<GraphEditChat workflowId={39} />);
        await request();
        expect(screen.getByText("Appointment date / Step added")).toBeTruthy();
        expect(screen.getByText("Appointment date / Connection added")).toBeTruthy();
        expect(useWorkflowStore.getState().nodes).toEqual(original);
        fireEvent.click(screen.getByRole("button", { name: "Apply to draft" }));
        expect(useWorkflowStore.getState().edges).toEqual([edge]);
        expect(useWorkflowStore.getState().nodes).toEqual([...original, added]);
        fireEvent.click(screen.getByRole("button", { name: "Undo" }));
        expect(useWorkflowStore.getState().nodes).toEqual(original);
        expect(useWorkflowStore.getState().edges).toEqual([]);
        fireEvent.click(screen.getByRole("button", { name: "Redo" }));
        expect(useWorkflowStore.getState().edges).toEqual([edge]);
        expect(mocks.post).toHaveBeenCalledTimes(1);
    });
    it("rejects malformed change descriptions without mutating the draft", async () => {
        mocks.post.mockResolvedValue({ data: { ...reply, changes: [{ node_id: "agent", field: "node_added", before: {}, after: "New" }] } });
        render(<GraphEditChat workflowId={39} />);
        fireEvent.change(screen.getByLabelText("What should change?"), { target: { value: "Add a step" } });
        fireEvent.click(screen.getByRole("button", { name: "Propose edit" }));
        await screen.findByRole("alert");
        expect(screen.queryByRole("button", { name: "Apply to draft" })).toBeNull();
        expect(useWorkflowStore.getState().isDirty).toBe(false);
    });
    it("shows a removed step name and restores it with its connections", async () => {
        const removed = { ...node("Collect details"), id: "details", data: { name: "Details", prompt: "Collect details" } } as FlowNode;
        const originalEdges = [{ id: "details-edge", source: "agent", target: "details" }] as FlowEdge[];
        useWorkflowStore.setState({ nodes: [node("Old"), removed], edges: originalEdges });
        mocks.post.mockResolvedValue({ data: {
            ...reply, graph: { nodes: [node("Old")], edges: [] },
            changes: [{ node_id: "details", field: "node_removed", before: "Collect details", after: null }],
        } });
        render(<GraphEditChat workflowId={39} />);
        await request();
        expect(screen.getByText("Details / Step removed")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Apply to draft" }));
        expect(useWorkflowStore.getState().nodes).toEqual([node("Old")]);
        fireEvent.click(screen.getByRole("button", { name: "Undo" }));
        expect(useWorkflowStore.getState().nodes).toEqual([node("Old"), removed]);
        expect(useWorkflowStore.getState().edges).toEqual(originalEdges);
    });
    it.each([
        { nodes: [{ id: "new" }], edges: [] },
        { nodes: [node("Old")], edges: [{ id: "broken", source: "agent", target: "missing" }] },
    ])("rejects malformed graph previews without crashing", async (graph) => {
        mocks.post.mockResolvedValue({ data: { ...reply, graph } });
        render(<GraphEditChat workflowId={39} />);
        fireEvent.change(screen.getByLabelText("What should change?"), { target: { value: "Add a step" } });
        fireEvent.click(screen.getByRole("button", { name: "Propose edit" }));
        await screen.findByRole("alert");
        expect(screen.queryByRole("button", { name: "Apply to draft" })).toBeNull();
        expect(useWorkflowStore.getState().nodes).toEqual([node("Old")]);
    });
    it("discards without mutating the draft", async () => {
        mocks.post.mockResolvedValue({ data: reply });
        render(<GraphEditChat workflowId={39} />);
        await request();
        fireEvent.click(screen.getByRole("button", { name: "Discard" }));
        expect(useWorkflowStore.getState().isDirty).toBe(false);
        expect(screen.queryByRole("button", { name: "Apply to draft" })).toBeNull();
    });
    it("normalizes resolved HTTP errors and preserves the draft", async () => {
        mocks.post.mockResolvedValue({ error: { detail: [{ msg: "Invalid graph", loc: ["body", "graph"] }] } });
        render(<GraphEditChat workflowId={39} />);
        fireEvent.change(screen.getByLabelText("What should change?"), { target: { value: "Warm" } });
        fireEvent.click(screen.getByRole("button", { name: "Propose edit" }));
        await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Invalid graph"));
        expect(useWorkflowStore.getState().isDirty).toBe(false);
    });
    it("waits for auth and disables historical editing", () => {
        mocks.auth.loading = true;
        const { rerender } = render(<GraphEditChat workflowId={39} />);
        fireEvent.change(screen.getByLabelText("What should change?"), { target: { value: "Warm" } });
        expect((screen.getByRole("button", { name: "Propose edit" }) as HTMLButtonElement).disabled).toBe(true);
        mocks.auth.loading = false;
        rerender(<GraphEditChat workflowId={39} readOnly />);
        expect((screen.getByLabelText("What should change?") as HTMLTextAreaElement).disabled).toBe(true);
        expect(mocks.post).not.toHaveBeenCalled();
    });
    it("does not submit the previous agent while the next draft loads", () => {
        render(<GraphEditChat workflowId={40} />);
        expect((screen.getByRole("button", { name: "Propose edit" }) as HTMLButtonElement).disabled).toBe(true);
        expect(screen.getByText(/Loading this agent/)).toBeTruthy();
        expect(mocks.post).not.toHaveBeenCalled();
    });
    it("carries the original request when answering a clarification", async () => {
        mocks.post.mockResolvedValueOnce({ data: { status: "clarification", summary: "Which step?", graph: null, changes: [] } }).mockResolvedValueOnce({ data: reply });
        render(<GraphEditChat workflowId={39} />);
        fireEvent.change(screen.getByLabelText("What should change?"), { target: { value: "Make it warmer" } });
        fireEvent.click(screen.getByRole("button", { name: "Propose edit" }));
        await screen.findByText("Which step?");
        fireEvent.change(screen.getByLabelText("What should change?"), { target: { value: "The greeting" } });
        fireEvent.click(screen.getByRole("button", { name: "Propose edit" }));
        await screen.findByRole("button", { name: "Apply to draft" });
        expect(mocks.post.mock.calls[1][0].body.message).toContain("Make it warmer");
        expect(mocks.post.mock.calls[1][0].body.message).toContain("The greeting");
    });
});
