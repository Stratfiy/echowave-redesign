import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { orderSteps, ReadOnlyStepList } from "../ReadOnlyStepList";

const nodes = [
    { id: "end", type: "endCall", data: { name: "Goodbye" } },
    { id: "s", type: "startCall", data: { name: "Greeting", prompt: "Say hello in Tamil" } },
    { id: "a", type: "agentNode", data: { name: "Book appointment", invalid: true, validationMessage: "Needs a calendar" } },
    { id: "orphan", type: "globalNode", data: { name: "House rules" } },
];
const edges = [
    { source: "s", target: "a" },
    { source: "a", target: "end" },
];

describe("ReadOnlyStepList", () => {
    it("orders steps the way a run meets them, unreachable ones last but listed", () => {
        expect(orderSteps(nodes, edges).map((n) => n.id)).toEqual(["s", "a", "end", "orphan"]);
    });

    it("shows each step readably, its next step, and what needs fixing", () => {
        render(<ReadOnlyStepList workflowId={7} name="Front desk" nodes={nodes} edges={edges} versionStatus="draft" totalRuns={3} />);
        const steps = screen.getAllByTestId("step");
        expect(steps).toHaveLength(4);
        expect(steps[0].textContent).toContain("Greeting");
        expect(steps[0].textContent).toContain("Then: Book appointment");
        expect(screen.getByText("Needs a calendar")).toBeTruthy();
        expect(screen.getByText(/Draft version · 3 runs/)).toBeTruthy();
        expect(screen.getByText(/Editing the flow needs a larger screen/)).toBeTruthy();
        expect(screen.getByRole("link", { name: /Runs/ }).getAttribute("href")).toBe("/workflow/7/runs");
        // Read-only: nothing on it saves.
        expect(screen.queryByRole("button", { name: /Save|Publish/ })).toBeNull();
    });

    it("still lets someone open the full editor", () => {
        const onOpenEditor = vi.fn();
        render(<ReadOnlyStepList workflowId={7} name="x" nodes={nodes} edges={edges} onOpenEditor={onOpenEditor} />);
        fireEvent.click(screen.getByRole("button", { name: "Open the editor anyway" }));
        expect(onOpenEditor).toHaveBeenCalledOnce();
    });
});
