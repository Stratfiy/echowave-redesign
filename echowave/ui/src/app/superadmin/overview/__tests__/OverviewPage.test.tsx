import { render, screen, within } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import type { Loaded } from "@/lib/staff/data";

const OVERVIEW = {
    observed_at: "2026-10-08T10:00:00Z",
    attention_state: "ok",
    attention: [
        { key: "kyc_waiting", title: "KYC submissions waiting for review", count: 0, href: "/superadmin/verification", severity: "info" },
        { key: "support_escalations", title: "Support escalations", count: null, href: "/superadmin/support", severity: "info", state: "needs_setup", reason: "The support inbox is built by the support stream." },
        { key: "failed_tasks", title: "Tasks failed or with an unknown outcome (24 h)", count: 2, href: "/superadmin/operations?tab=jobs", severity: "critical" },
    ],
    health: { state: "unknown", signals: [{ name: "worker", state: "unknown" }] },
    metrics: {
        state: "ok",
        period: "Last 7 days",
        weekly_useful_users: { value: 3, sample: 5, definition: "d" },
        task_success: { value: null, sample: 0, definition: "d" },
        usefulness: { value: 0.5, sample: 4, definition: "d" },
    },
    recent_failures: [],
    recent_commands: [],
    evidence_state: "ok",
};

vi.mock("@/lib/staff/data", () => ({
    useStaffData: (url: string | null): Loaded<unknown> => {
        const base = { error: null, refreshedAt: new Date(), refresh: async () => {} };
        if (url === "/api/v1/admin/staff/overview") return { ...base, state: "ok", data: OVERVIEW };
        if (url === "/api/v1/admin/staff/commands") return { ...base, state: "ok", data: { commands: [] } };
        return { ...base, state: "needs_setup", data: null, error: "Not Found" };
    },
    staffPost: vi.fn(),
}));
vi.mock("@/components/staff/StaffShell", () => ({
    useReportFreshness: () => {},
    useStaffConsole: () => ({ me: { user_id: 1, roles: ["owner"], environment: "staging" }, can: () => true, days: 28 }),
}));

import OverviewPage from "../page";

describe("the founder overview", () => {
    it("puts open exceptions first and still shows what cannot be measured", () => {
        render(<OverviewPage />);
        const rows = within(screen.getByTestId("attention")).getAllByRole("listitem");
        expect(rows.map((r) => r.getAttribute("data-key"))).toEqual(["failed_tasks", "kyc_waiting", "support_escalations"]);
        expect(rows[0].querySelector("a")?.getAttribute("href")).toBe("/superadmin/operations?tab=jobs");
        expect(rows[2].textContent).toContain("support stream");
        expect(rows[2].querySelector("a")).toBeNull();
    });

    it("never shows unknown health as healthy, and shows ops as needs setup", () => {
        render(<OverviewPage />);
        expect(document.querySelector('[data-state="unknown"]')).not.toBeNull();
        expect(document.querySelector('[data-state="healthy"]')).toBeNull();
        expect(screen.getByText("Needs setup (ops console)")).toBeTruthy();
    });

    it("shows a metric with no denominator as a dash, not 0%", () => {
        render(<OverviewPage />);
        expect(screen.getByLabelText("Task success: not available")).toBeTruthy();
        expect(screen.getByLabelText("Weekly useful users: 3")).toBeTruthy();
        expect(screen.getByLabelText("Usefulness: 50.0%")).toBeTruthy();
    });
});
