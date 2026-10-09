import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Loaded } from "@/lib/staff/data";

const api = vi.hoisted(() => ({
    get: new Map<string, { ok: boolean; status: number; data?: unknown; error?: string }>(),
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "u" }, loading: false }) }));
vi.mock("@/lib/staff/data", async () => {
    const actual = await vi.importActual<typeof import("@/lib/staff/data")>("@/lib/staff/data");
    function useStaffData<T>(url: string | null): Loaded<T> {
        const [state, setState] = React.useState<Loaded<T>>({ state: "loading", data: null, error: null, refreshedAt: null, refresh: async () => {} });
        React.useEffect(() => {
            if (!url) return;
            const r = api.get.get(url) ?? { ok: false, status: 404, error: "Not Found" };
            setState({
                state: r.ok ? "ok" : actual.stateFor(r.status, false),
                data: (r.ok ? r.data : null) as T | null,
                error: r.ok ? null : (r.error ?? null),
                refreshedAt: r.ok ? new Date() : null,
                refresh: async () => {},
            });
        }, [url]);
        return state;
    }
    return { ...actual, useStaffData };
});

import { OpsAlertsPanel } from "../OpsAlertsPanel";

const ALERTS = "/api/v1/admin/ops/alerts";
const SUMMARIES = "/api/v1/admin/ops/alerts/daily-summaries";

const open = {
    observed_at: "2026-10-08T09:30:00+00:00",
    open: [
        {
            key: "calls:failure_spike",
            title: "Call failures at 58% of the last 12 calls",
            detail: "7 of 12 finished carrier calls failed.",
            severity: "critical",
            opened_at: "2026-10-08T09:25:00+00:00",
        },
    ],
    resolved: [],
    ticks: [{ name: "fire_due_routines", label: "Routines", last_completed: null }],
};

const summary = {
    day: "2026-10-07",
    total_paise: 2550,
    average_paise: 700,
    delta_pct: 264.3,
    lines: [
        { key: "numbers", label: "Phone number rental", unit: "number-months", units: 1, cost_paise: 1500, unpriced_units: 0, no_rate: false },
        { key: "telephony", label: "Telephony", unit: "seconds", units: 600, cost_paise: 0, unpriced_units: 600, no_rate: true },
    ],
    top_organizations: [{ organization_id: 2, name: "Beta Traders", cost_paise: 1800 }],
    top_agents: [],
    calls: { finished: 2, answered: 1, not_connected: 1, carrier_failed: 0, unknown: 0, in_progress: 0 },
    active_organizations: 2,
    mailed_at: "2026-10-08T03:15:00+00:00",
};

describe("OpsAlertsPanel", () => {
    beforeEach(() => api.get.clear());

    it("shows what is open, each tick and the summaries, with no-rate usage said", async () => {
        api.get.set(ALERTS, { ok: true, status: 200, data: open });
        api.get.set(SUMMARIES, { ok: true, status: 200, data: { summaries: [summary] } });
        render(<OpsAlertsPanel />);
        expect(await screen.findByText("Call failures at 58% of the last 12 calls")).toBeTruthy();
        expect(screen.getByText("7 of 12 finished carrier calls failed.")).toBeTruthy();
        expect(screen.getByText("no completion on record")).toBeTruthy();
        const day = await screen.findByTestId("ops-summary-2026-10-07");
        expect(day.textContent).toContain("no rate");
        expect(day.textContent).toContain("10 min");
        expect(day.textContent).toContain("Beta Traders");
        expect(day.textContent).toContain("+264%");
    });

    it("says nothing is open rather than drawing an empty list", async () => {
        api.get.set(ALERTS, { ok: true, status: 200, data: { ...open, open: [] } });
        render(<OpsAlertsPanel />);
        expect(await screen.findByText(/Nothing open\./)).toBeTruthy();
    });

    it("tells a support role the summaries are not theirs, and says off when off", async () => {
        api.get.set(SUMMARIES, { ok: false, status: 403, error: "Forbidden" });
        render(<OpsAlertsPanel />);
        await waitFor(() => expect(screen.getByText(/Your role does not include this/)).toBeTruthy());
        expect(screen.getAllByTestId("needs-setup").length).toBeGreaterThan(0);
    });
});
