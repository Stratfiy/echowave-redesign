import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Loaded } from "@/lib/staff/data";

const route = vi.hoisted(() => ({ pathname: "/superadmin/overview" }));
const api = vi.hoisted(() => ({
    get: new Map<string, { ok: boolean; status: number; data?: unknown; error?: string }>(),
    posts: [] as Array<{ url: string; body: unknown }>,
    post: new Map<string, { ok: boolean; status: number; data?: unknown; error?: string }>(),
}));

vi.mock("next/navigation", () => ({
    usePathname: () => route.pathname,
    useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({}),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "u" }, loading: false }) }));
vi.mock("@/lib/staff/data", async () => {
    const actual = await vi.importActual<typeof import("@/lib/staff/data")>("@/lib/staff/data");
    const staffGet = async (url: string) => api.get.get(url) ?? { ok: false, status: 404, error: "Not Found" };
    const staffPost = async (url: string, body: unknown) => {
        api.posts.push({ url, body });
        return api.post.get(url) ?? { ok: false, status: 404, error: "Not Found" };
    };
    function useStaffData<T>(url: string | null): Loaded<T> {
        const [state, setState] = React.useState<Loaded<T>>({ state: "loading", data: null, error: null, refreshedAt: null, refresh: async () => {} });
        React.useEffect(() => {
            if (!url) return;
            void staffGet(url).then((r) =>
                setState({
                    state: r.ok ? "ok" : actual.stateFor(r.status, false),
                    data: (r.ok ? r.data : null) as T | null,
                    error: r.ok ? null : (r.error ?? null),
                    refreshedAt: r.ok ? new Date() : null,
                    refresh: async () => {},
                }),
            );
        }, [url]);
        return state;
    }
    return { ...actual, staffGet, staffPost, useStaffData };
});

import { CommandFlow } from "../CommandFlow";
import { Panel } from "../parts";
import { StaffShell } from "../StaffShell";

const DEST = ["overview", "users", "support", "quality", "analytics", "revenue", "operations", "controls"];

function meFor(roles: string[], allowed: string[]) {
    return {
        ok: true,
        status: 200,
        data: {
            user_id: 7,
            email: "staff@example.test",
            tier: "support",
            mfa_enabled: true,
            roles,
            capabilities: ["overview.read", "refunds.request"],
            destinations: DEST.map((key) => ({ key, label: key, href: `/superadmin/${key}`, allowed: allowed.includes(key) })),
            environment: "staging",
            features: {},
        },
    };
}

beforeEach(() => {
    api.get.clear();
    api.post.clear();
    api.posts.length = 0;
    route.pathname = "/superadmin/overview";
});

describe("StaffShell", () => {
    it("draws only the destinations the role allows, with the environment pinned", async () => {
        api.get.set("/api/v1/admin/staff/me", meFor(["finance", "support"], ["overview", "revenue"]));
        render(
            <StaffShell>
                <p>page</p>
            </StaffShell>,
        );
        await screen.findByText("page");
        const nav = screen.getAllByRole("navigation", { name: "Staff console" })[0];
        expect(nav.textContent).toContain("Overview");
        expect(nav.textContent).toContain("Revenue and costs");
        expect(nav.textContent).not.toContain("Controls and audit");
        expect(screen.getByTestId("staff-environment").textContent).toBe("staging");
    });

    it("refuses a page outside the role in words, without rendering it", async () => {
        route.pathname = "/superadmin/controls/roles";
        api.get.set("/api/v1/admin/staff/me", meFor(["support"], ["overview", "users"]));
        render(
            <StaffShell>
                <p>secret page</p>
            </StaffShell>,
        );
        expect(await screen.findByText("Your role does not include this page")).toBeTruthy();
        expect(screen.queryByText("secret page")).toBeNull();
    });

    it("says the console needs setup when the flag is off on the server", async () => {
        render(
            <StaffShell>
                <p>page</p>
            </StaffShell>,
        );
        expect(await screen.findByText("The staff console is not switched on here")).toBeTruthy();
    });
});

function loaded<T>(over: Partial<Loaded<T>>): Loaded<T> {
    return { state: "ok", data: null, error: null, refreshedAt: new Date(), refresh: async () => {}, ...over } as Loaded<T>;
}

describe("Panel", () => {
    it("says a failure is not an empty result", () => {
        render(<Panel query={loaded<string>({ state: "failed", error: "boom" })}>{(d) => <p>{d}</p>}</Panel>);
        expect(screen.getByRole("alert").textContent).toContain("not the same as nothing to show");
    });

    it("keeps stale values on screen under a warning", () => {
        render(<Panel query={loaded<string>({ state: "stale", data: "old figures", error: "timeout" })}>{(d) => <p>{d}</p>}</Panel>);
        expect(screen.getByText("old figures")).toBeTruthy();
        expect(screen.getByRole("status").textContent).toContain("Refresh failed");
    });

    it("shows needs setup for a route that is not there", () => {
        render(<Panel query={loaded<string>({ state: "needs_setup" })} setupHint="Ops is not here.">{(d) => <p>{d}</p>}</Panel>);
        expect(screen.getByTestId("needs-setup").textContent).toContain("Ops is not here.");
    });
});

describe("CommandFlow", () => {
    async function renderFlow() {
        api.get.set("/api/v1/admin/staff/me", meFor(["finance"], ["overview", "revenue"]));
        render(
            <StaffShell>
                <CommandFlow command="refund.request" target={{ payment_id: 1, organization_id: 2, amount_minor: 500 }} targetLabel="Acme: payment #1" />
            </StaffShell>,
        );
    }

    it("shows the refusal instead of a Run button when not eligible", async () => {
        api.post.set("/api/v1/admin/staff/commands/preview", { ok: true, status: 200, data: { eligible: false, refusal: "Refunds need setup.", preview: null, requires_approval: true, execution: "worker", summary: "Refund", environment: "staging", roles: ["finance"] } });
        await renderFlow();
        expect((await screen.findByTestId("command-refused")).textContent).toContain("Refunds need setup.");
        expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    });

    it("sends one idempotency key and says it is waiting for a second person", async () => {
        api.post.set("/api/v1/admin/staff/commands/preview", { ok: true, status: 200, data: { eligible: true, refusal: null, preview: { eligible_minor: 1000 }, requires_approval: true, execution: "worker", summary: "Refund part of a payment", environment: "staging", roles: ["finance"] } });
        api.post.set("/api/v1/admin/staff/commands", { ok: true, status: 202, data: { id: 9, command: "refund.request", state: "awaiting_approval", environment: "staging", preview: null, result: null, reason_code: null, approval_required: true, requested_by: 7, approved_by: null } });
        await renderFlow();
        expect(await screen.findByText("Refund part of a payment")).toBeTruthy();
        expect(screen.getByLabelText("Exact effect").textContent).toContain("1000");
        fireEvent.change(screen.getByLabelText("Reason (recorded in the audit)"), { target: { value: "Charged twice" } });
        await act(async () => {
            fireEvent.click(screen.getByRole("button", { name: "Run" }));
        });
        await waitFor(() => expect(screen.getByTestId("command-state").getAttribute("data-state")).toBe("awaiting_approval"));
        expect(screen.getByTestId("command-state").textContent).toContain("waiting for a second person");
        const sent = api.posts.find((p) => p.url === "/api/v1/admin/staff/commands")?.body as Record<string, string>;
        expect(sent.reason).toBe("Charged twice");
        expect(sent.environment).toBe("staging");
        expect(sent.idempotency_key).toMatch(/^refund\.request-/);
        // Accepted is not done: no success line.
        expect(screen.queryByText("Done.")).toBeNull();
    });
});
