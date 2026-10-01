import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { actionLabel, AuditLog } from "../AuditLog";

const api = vi.hoisted(() => ({ read: vi.fn() }));

vi.mock("@/client/sdk.gen", () => ({ readAuditLogApiV1AdminAuditGet: api.read }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("next/link", () => ({
    default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

function entry(n: number, over: Record<string, unknown> = {}) {
    return {
        key: `admin:${n}`,
        source: "admin",
        id: n,
        created_at: `2026-09-0${n}T10:00:00Z`,
        actor_user_id: 1,
        actor_email: "staff@decibyl.example",
        action: "trial_end_set",
        organization_id: 5,
        organization_name: "Sunrise Dental",
        target_user_id: null,
        target_user_email: null,
        note: `note ${n}`,
        old_value: null,
        new_value: null,
        ...over,
    };
}

beforeEach(() => {
    api.read.mockReset();
});

describe("AuditLog", () => {
    it("lists both logs and pages older entries by cursor", async () => {
        api.read
            .mockResolvedValueOnce({
                data: {
                    entries: [entry(3), entry(2, { source: "billing", key: "billing:2", action: "credit_adjusted", old_value: { b: 0 }, new_value: { b: 1 } })],
                    next_before: "2026-09-02T10:00:00+00:00~billing~2",
                    actions: ["credit_adjusted", "trial_end_set"],
                },
            })
            .mockResolvedValueOnce({
                data: { entries: [entry(1)], next_before: null, actions: [] },
            });

        render(<AuditLog showFilters />);

        expect(await screen.findByText("note 3")).toBeTruthy();
        expect(screen.getByText("Credit adjusted")).toBeTruthy();
        expect(screen.getByText("Billing")).toBeTruthy();
        expect(screen.getAllByText("Sunrise Dental").length).toBe(2);

        fireEvent.click(screen.getByRole("button", { name: /load older entries/i }));
        expect(await screen.findByText("note 1")).toBeTruthy();
        expect(api.read.mock.calls[1][0].query.before).toBe("2026-09-02T10:00:00+00:00~billing~2");
        expect(screen.queryByRole("button", { name: /load older entries/i })).toBeNull();
    });

    it("on an account page asks only for that account and drops the account column", async () => {
        api.read.mockResolvedValue({ data: { entries: [entry(1)], next_before: null, actions: [] } });
        render(<AuditLog organizationId={5} />);
        expect(await screen.findByText("note 1")).toBeTruthy();
        expect(api.read.mock.calls[0][0].query.organization_id).toBe(5);
        expect(screen.queryByText("Sunrise Dental")).toBeNull();
    });

    it("says when there is nothing, and shows an error rather than an empty table", async () => {
        api.read.mockResolvedValueOnce({ data: { entries: [], next_before: null, actions: [] } });
        const { unmount } = render(<AuditLog organizationId={5} />);
        expect(await screen.findByText("Nothing recorded for these filters yet.")).toBeTruthy();
        unmount();

        api.read.mockResolvedValueOnce({ error: { detail: "Access denied" }, response: { status: 403 } });
        render(<AuditLog organizationId={5} />);
        await waitFor(() => expect(screen.getByText(/Access denied/)).toBeTruthy());
    });

    it("words actions for people", () => {
        expect(actionLabel("impersonation_stopped")).toBe("Impersonation stopped");
    });
});
