/**
 * The chip's tooltip names what a low balance stops: calls on a voice plan,
 * replies on a text-only one. "Too low to place calls" on a plan with no
 * phone line reads as a feature the account never had.
 */

import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getBalance = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({ getBalanceApiV1BillingBalanceGet: getBalance }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/components/ui/tooltip", () => ({
    Tooltip: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    TooltipTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    TooltipContent: ({ children }: { children: React.ReactNode }) => <div data-testid="tip">{children}</div>,
}));

import { BalanceChip } from "../BalanceChip";

const empty = { balance_paise: 0, min_balance_paise: 2000, calling_blocked: true, suggested_topup_paise: null };

beforeEach(() => vi.clearAllMocks());

describe("the balance chip", () => {
    it("says replies stop on a text-only plan", async () => {
        getBalance.mockResolvedValue({ data: { ...empty, voice_allowed: false } });
        render(<BalanceChip />);
        await waitFor(() => expect(screen.getByTestId("tip").textContent).toContain("Too low to reply"));
        expect(screen.getByTestId("tip").textContent).toContain("start replying again");
    });

    it("says calls stop on a voice plan", async () => {
        getBalance.mockResolvedValue({ data: { ...empty, voice_allowed: true } });
        render(<BalanceChip />);
        await waitFor(() => expect(screen.getByTestId("tip").textContent).toContain("Too low to place calls"));
    });
});
