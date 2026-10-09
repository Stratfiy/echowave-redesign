import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { TrialLine } from "../TrialNotice";

describe("the trial line (PLAN-1)", () => {
    it("says nothing for an account that is not on the trial", () => {
        const { container } = render(
            <TrialLine trial={{ on_trial: false, active: false, starts_at: null, ends_at: null, days_left: null, days: 14 }} />,
        );
        expect(container.textContent).toBe("");
    });

    it("shows the end date and days left while it runs", () => {
        render(
            <TrialLine
                trial={{ on_trial: true, active: true, starts_at: "2026-10-04T00:00:00Z", ends_at: "2026-10-18T00:00:00Z", days_left: 9, days: 14 }}
            />,
        );
        const line = screen.getByTestId("trial-active");
        expect(line.textContent).toContain("18 October");
        expect(line.textContent).toContain("9 days left");
        expect(line.textContent).not.toMatch(/plans/i);
    });

    it("after the end, says the data is still there and points at help, not plans", () => {
        render(
            <TrialLine
                trial={{ on_trial: true, active: false, starts_at: "2026-10-04T00:00:00Z", ends_at: "2026-10-18T00:00:00Z", days_left: 0, days: 14 }}
            />,
        );
        const line = screen.getByTestId("trial-ended");
        expect(line.textContent).toContain("still here");
        // No pricing is shown to users (lib/pricing.ts): no plan link.
        expect(screen.queryByText("See plans")).toBeNull();
        expect(line.textContent).not.toMatch(/plan/i);
        expect(screen.getByText("Get help").getAttribute("href")).toBe("/help");
    });
});
