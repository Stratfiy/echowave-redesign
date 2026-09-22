import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { VendorSplit } from "../VendorSplit";

const report = vi.hoisted(() => ({
    by_model: [
        { source: "direct", provider: "anthropic", model: "claude-x", calls: 3, input_tokens: 1000, cached_tokens: 9000, cache_write_tokens: 0, output_tokens: 400, total_tokens: 10400, cached_share: 0.9, vendor_cost_paise: 720, unpriced: [] },
        { source: "run", provider: "mystery", model: "m", calls: 1, input_tokens: 10, cached_tokens: 0, cache_write_tokens: 0, output_tokens: 1, total_tokens: 11, cached_share: 0, vendor_cost_paise: 0, unpriced: ["llm_input"] },
    ],
    by_work: [{ source: "direct", work: "decibyl", tokens_per_call: { count: 3, median: 3000, p90: 5000 }, vendor_paise_per_call: { count: 3, median: 200, p90: 400 } }],
    totals: { vendor_cost_paise: 720, tokens: 10411, direct_calls: 3, unattributed_calls: 1 },
    not_metered: ["channel context fold"],
}));

vi.mock("@/client/sdk.gen", () => ({ tokenUsageReportApiV1AdminBillingTokensByModelGet: vi.fn(async () => ({ data: report })) }));
vi.mock("@/components/charts/primitives", () => ({
    useAuthReady: () => true,
    LoadingBlock: () => <div>loading</div>,
    PanelMessage: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    StatTile: ({ label, value, sub }: { label: string; value: string; sub?: string }) => <div>{label}: {value} {sub}</div>,
}));

describe("the vendor split", () => {
    it("shows each vendor's four lines, the work, the gaps and what it cannot see", async () => {
        render(<VendorSplit />);
        await waitFor(() => expect(screen.getByText("claude-x")).toBeTruthy());
        expect(screen.getByText("9.0k")).toBeTruthy();
        expect(screen.getByText("90%")).toBeTruthy();
        expect(screen.getByText("Decibyl assistant")).toBeTruthy();
        expect(screen.getByText(/1 of 3/)).toBeTruthy();
        expect(screen.getByText(/\+ unpriced/)).toBeTruthy();
        expect(screen.getByText(/Not metered yet: channel context fold/)).toBeTruthy();
    });
});
