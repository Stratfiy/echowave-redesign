import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuditTimeline } from "../AuditTimeline";
import { CommandPreview } from "../CommandPreview";
import { MetricDefinition } from "../MetricDefinition";

describe("MetricDefinition", () => {
    it("shows a missing value as a dash with its reason, never zero", () => {
        render(<MetricDefinition name="Activation" value={null} definition="People who finished a task" missingReason="No data yet" />);
        expect(screen.getByText("—")).toBeTruthy();
        expect(screen.getByText("No data yet")).toBeTruthy();
        expect(screen.queryByText("0")).toBeNull();
    });

    it("opens how the number is counted", () => {
        render(<MetricDefinition name="Activation" value={42} unit="%" definition="Finished one useful task" source="task_ledger.completed" period="Last 7 days" />);
        const toggle = screen.getByRole("button", { name: "How this is counted" });
        expect(screen.getByText("Finished one useful task").closest("[hidden]")).not.toBeNull();
        fireEvent.click(toggle);
        expect(toggle.getAttribute("aria-expanded")).toBe("true");
        expect(screen.getByText("Finished one useful task").closest("[hidden]")).toBeNull();
        expect(screen.getByLabelText("Activation: 42 %")).toBeTruthy();
    });
});

describe("AuditTimeline", () => {
    it("lists newest first with actor, action, reason and result", () => {
        render(
            <AuditTimeline
                entries={[
                    { id: 1, at: "2026-10-01T10:00:00Z", actor: "Asha", action: "flag_changed", target: "chat_shell" },
                    { id: 2, at: "2026-10-02T10:00:00Z", actor: "Ravi", action: "Refunded ₹500", reason: "Duplicate charge", result: "Succeeded" },
                ]}
            />,
        );
        const rows = screen.getAllByTestId("audit-entry");
        expect(rows[0].textContent).toContain("Ravi");
        expect(rows[0].textContent).toContain("Reason: Duplicate charge");
        expect(rows[1].textContent).toContain("flag changed");
    });

    it("says so when nothing has happened", () => {
        render(<AuditTimeline entries={[]} />);
        expect(screen.getByText("Nothing has been done here yet.")).toBeTruthy();
    });
});

describe("CommandPreview", () => {
    const command = {
        command: "restart-worker --pool voice",
        role: "operator",
        environment: "production",
        target: "voice-worker-2",
        idempotencyKey: "op-7f1c",
    };

    it("needs a reason before it runs, and passes it on", () => {
        const onConfirm = vi.fn();
        render(<CommandPreview command={command} state="ready" onConfirm={onConfirm} />);
        const run = screen.getByRole("button", { name: "Run" }) as HTMLButtonElement;
        expect(run.disabled).toBe(true);
        fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "Stuck after deploy" } });
        fireEvent.click(run);
        expect(onConfirm).toHaveBeenCalledWith("Stuck after deploy");
    });

    it("says accepted is not finished", () => {
        render(<CommandPreview command={command} state="accepted" onConfirm={vi.fn()} />);
        expect(screen.getByRole("status").textContent).toContain("Accepted, not finished");
        expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    });
});
