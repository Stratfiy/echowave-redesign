import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RateCardSection, RunwayLine } from "../RateCardSection";

const flags: Record<string, boolean> = {};
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(flags[name]) }));

const rateCard = vi.fn();
vi.mock("@/client/sdk.gen", () => ({
    getRateCardApiV1BillingRateCardGet: (...args: unknown[]) => rateCard(...args),
}));

const CARD = {
    enabled: true,
    version: "2026-09-25",
    effective_from: "2026-09-25",
    paise_per_credit: 50,
    included_model_paise_per_credit: 14.71,
    premium_model_multiplier: 3.4,
    lines: [
        { key: "text_reply", label: "Text reply", credits: 1, unit: "per reply", notes: "Includes the AI model cost of a normal reply.", basis: "compute" },
        { key: "voice_minute", label: "Voice minute, standard voice", credits: 12, unit: "per minute", notes: "", basis: "market" },
        { key: "premium_model_tokens", label: "Premium model", credits: null, unit: "per event", notes: "x 3.4", basis: "compute" },
    ],
};

beforeEach(() => {
    rateCard.mockReset();
    for (const key of Object.keys(flags)) delete flags[key];
});

describe("RateCardSection", () => {
    it("shows nothing, and asks nothing, while the charge rule is off", () => {
        const { container } = render(<RateCardSection />);
        expect(container.textContent).toBe("");
        expect(rateCard).not.toHaveBeenCalled();
    });

    it("lists what things cost when the rule is on", async () => {
        flags.charge_rule = true;
        rateCard.mockResolvedValue({ data: CARD });
        render(<RateCardSection />);
        await waitFor(() => expect(screen.getByText("What things cost")).toBeTruthy());
        expect(screen.getByText("Text reply")).toBeTruthy();
        expect(screen.getByText("12")).toBeTruthy();
        // A line priced by formula says so rather than printing a number.
        expect(screen.getByText("Model tokens")).toBeTruthy();
        expect(screen.getByText(/1 credit = ₹0.50/)).toBeTruthy();
    });

    it("says so when the card cannot be read", async () => {
        flags.charge_rule = true;
        rateCard.mockResolvedValue({ error: { detail: "nope" } });
        render(<RateCardSection />);
        await waitFor(() => expect(screen.getByText(/Could not load the rate card/)).toBeTruthy());
    });
});

describe("RunwayLine", () => {
    it("is hidden while the rule is off", () => {
        const { container } = render(<RunwayLine balancePaise={60_000} />);
        expect(container.textContent).toBe("");
    });

    it("turns the balance into replies and voice minutes", () => {
        flags.charge_rule = true;
        render(<RunwayLine balancePaise={60_000} />);
        // 1,200 credits: 1,200 replies at 1, 100 minutes at 12.
        expect(screen.getByText("≈ 1,200 replies · ≈ 100 voice minutes left")).toBeTruthy();
    });

    it("never shows a negative runway", () => {
        flags.charge_rule = true;
        render(<RunwayLine balancePaise={-500} />);
        expect(screen.getByText("≈ 0 replies · ≈ 0 voice minutes left")).toBeTruthy();
    });
});
