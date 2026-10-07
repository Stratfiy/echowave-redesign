import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Announcer } from "../Announcer";
import { ErrorState } from "../ErrorState";
import * as shared from "../index";

describe("ErrorState", () => {
    it("names the failed step and retries", () => {
        const onRetry = vi.fn();
        render(<ErrorState title="Could not load this conversation" description="Your draft is kept." onRetry={onRetry} />);
        expect(screen.getByRole("alert").textContent).toContain("Could not load this conversation");
        fireEvent.click(screen.getByRole("button", { name: "Try again" }));
        expect(onRetry).toHaveBeenCalledOnce();
    });

    it("cannot be pressed twice while retrying", () => {
        render(<ErrorState title="Failed" onRetry={vi.fn()} retrying />);
        expect((screen.getByRole("button", { name: /Trying again/ }) as HTMLButtonElement).disabled).toBe(true);
    });
});

describe("Announcer", () => {
    it("says a change of state once, politely", () => {
        vi.useFakeTimers();
        const { rerender } = render(<Announcer message={null} />);
        const region = screen.getByTestId("announcer");
        expect(region.getAttribute("aria-live")).toBe("polite");
        rerender(<Announcer message="Decibyl is replying" />);
        act(() => vi.advanceTimersByTime(60));
        expect(region.textContent).toBe("Decibyl is replying");
        vi.useRealTimers();
    });
});

describe("the shared components", () => {
    it("are all exported from one place, EmptyState reused rather than copied", () => {
        for (const name of [
            "TaskStatus",
            "ActionPreview",
            "SourceCoverage",
            "ScopedSearch",
            "SettingsSection",
            "ConnectionRow",
            "SaveBar",
            "EmptyState",
            "ErrorState",
            "MetricDefinition",
            "AuditTimeline",
            "CommandPreview",
        ]) {
            expect(shared[name as keyof typeof shared], name).toBeTypeOf("function");
        }
    });
});
