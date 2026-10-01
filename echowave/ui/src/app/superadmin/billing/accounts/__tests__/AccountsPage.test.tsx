import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import AccountsPage from "../page";

const api = vi.hoisted(() => ({ list: vi.fn() }));

vi.mock("@/client/sdk.gen", () => ({ listAccountsApiV1AdminBillingAccountsGet: api.list }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("next/link", () => ({
    default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

const base = {
    organization_id: 5,
    name: "Sunrise Dental",
    owner_email: "owner@sunrise.example",
    owner_role: "owner",
    member_count: 1,
    account_type: null,
    status: "active",
    billable_minutes: 0,
    revenue_paise: 0,
    provider_cost_paise: 0,
    margin_paise: 0,
    margin_pct: null,
    balance_paise: 500_000,
    platform_rate_mpaise: 300_000,
    platform_rate_micros_usd: null,
    pulse_seconds: 60,
    platform_rate_source: "global_default",
    platform_rate_is_override: false,
    last_active_day: null,
};

describe("the accounts table (Org 360)", () => {
    it("shows plan or trial, agents and linked apps on each row", async () => {
        api.list.mockResolvedValue({
            data: {
                accounts: [
                    {
                        ...base,
                        plan: "trial",
                        plan_is_paid: false,
                        trial_ends_at: null,
                        trial: {
                            on_trial: true,
                            active: true,
                            stage: "ending_soon",
                            notice_stage: "1_day",
                            starts_at: null,
                            ends_at: "2026-10-02T00:00:00Z",
                            days_left: 1,
                            override: false,
                        },
                        agents_count: 4,
                        live_agents_count: 2,
                        channels: { whatsapp: 1, telegram: 0, slack: 0, teams: 0 },
                        channels_linked: 1,
                        kyc_status: "not_started",
                        byok_keys_present: false,
                        byok_providers: [],
                    },
                ],
            },
        });

        render(<AccountsPage />);

        expect(await screen.findByText("Trial · 1 day left")).toBeTruthy();
        expect(screen.getByText("(2 live)")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Sort by Agents" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Sort by Apps" })).toBeTruthy();
    });
});
