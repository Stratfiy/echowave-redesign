import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TASK_STATE_LABEL, TASK_STATES } from "@/lib/shell/taskState";

import { TaskStatus } from "../TaskStatus";

describe("TaskStatus", () => {
    it("names every state in words, never by colour alone", () => {
        for (const state of TASK_STATES) {
            const { unmount } = render(<TaskStatus state={state} />);
            const badge = screen.getByTestId("task-status");
            expect(badge.getAttribute("data-state")).toBe(state);
            expect(badge.textContent).toContain(TASK_STATE_LABEL[state]);
            // An icon with every label.
            expect(badge.querySelector("svg")).not.toBeNull();
            unmount();
        }
    });

    it("shows the real stage while running and the evidence when done", () => {
        const { rerender } = render(<TaskStatus state="running" stage="Reading calendar" />);
        expect(screen.getByTestId("task-status").textContent).toContain("Reading calendar");
        rerender(<TaskStatus state="completed" stage="Reading calendar" evidence="Message id 4411" />);
        const done = screen.getByTestId("task-status").textContent ?? "";
        expect(done).toContain("Done");
        expect(done).toContain("Message id 4411");
        // A finished task does not keep saying what it was doing.
        expect(done).not.toContain("Reading calendar");
    });

    it("spins only while running, and the spin is marked as a continuous effect", () => {
        const { rerender } = render(<TaskStatus state="running" />);
        expect(screen.getByTestId("task-status").querySelector("svg")?.getAttribute("class")).toContain("motion-continuous");
        rerender(<TaskStatus state="failed" />);
        expect(screen.getByTestId("task-status").querySelector("svg")?.getAttribute("class")).not.toContain("animate-spin");
    });
});
