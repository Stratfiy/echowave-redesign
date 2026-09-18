/**
 * The Logs screen called an abandoned text chat "In Progress" for ever: a
 * chat only completes when the conversation reaches an end node, and a
 * person who stopped typing never gets there. Open, not in progress.
 */

import { describe, expect, it } from "vitest";

import { runStatusLabel } from "../WorkflowRunsTable";

describe("a run's status badge", () => {
    it("calls a finished run completed, whatever it was", () => {
        expect(runStatusLabel({ is_completed: true, mode: "textchat" })).toBe("Completed");
        expect(runStatusLabel({ is_completed: true, mode: "plivo" })).toBe("Completed");
    });

    it("calls an unfinished chat open, and only a call in progress", () => {
        expect(runStatusLabel({ is_completed: false, mode: "textchat" })).toBe("Open");
        expect(runStatusLabel({ is_completed: false, mode: "plivo" })).toBe("In Progress");
        expect(runStatusLabel({ is_completed: false, mode: "smallwebrtc" })).toBe("In Progress");
    });
});
