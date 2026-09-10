import { describe, expect, it } from "vitest";

import { type Balance, figure, PILL_TEXT, sortBalances } from "../ProviderBalances";

function balance(overrides: Partial<Balance>): Balance {
    return {
        provider: "deepgram",
        status: "ok",
        kind: null,
        amount: null,
        currency: null,
        used: null,
        limit: null,
        remaining: null,
        renews_at: null,
        detail: null,
        ...overrides,
    };
}

describe("sortBalances", () => {
    it("puts the accounts that need a top-up first", () => {
        // The whole point of the ordering: an empty account alphabetised
        // between two healthy ones is an emergency nobody sees in time.
        const rows = sortBalances([
            balance({ provider: "twilio", status: "ok" }),
            balance({ provider: "openai", status: "unsupported" }),
            balance({ provider: "plivo", status: "empty" }),
            balance({ provider: "elevenlabs", status: "low" }),
        ]);
        expect(rows.map((row) => row.provider)).toEqual([
            "plivo",
            "elevenlabs",
            "twilio",
            "openai",
        ]);
    });

    it("sorts by name within a status", () => {
        const rows = sortBalances([
            balance({ provider: "twilio", status: "low" }),
            balance({ provider: "deepgram", status: "low" }),
        ]);
        expect(rows.map((row) => row.provider)).toEqual(["deepgram", "twilio"]);
    });

    it("does not mutate what it was handed", () => {
        const input = [
            balance({ provider: "twilio", status: "ok" }),
            balance({ provider: "plivo", status: "empty" }),
        ];
        sortBalances(input);
        expect(input.map((row) => row.provider)).toEqual(["twilio", "plivo"]);
    });
});

describe("figure", () => {
    it("shows money with its currency", () => {
        expect(
            figure(balance({ kind: "money", remaining: 1234.5, currency: "usd" })),
        ).toBe("1,234.50 USD");
    });

    it("omits a currency the vendor never gave us", () => {
        // Plivo reports credits without saying whether they are rupees or
        // dollars. Stamping a symbol on that would be a guess read as fact.
        expect(figure(balance({ kind: "money", remaining: 1250.75 }))).toBe("1,250.75");
    });

    it("shows a quota against its ceiling", () => {
        expect(
            figure(balance({ kind: "quota", remaining: 10000, limit: 100000 })),
        ).toBe("10,000 of 100,000 characters");
    });

    it("renders nothing at all when there is no figure", () => {
        // Not "0". A blank next to OpenAI must not read as "no credit left"
        // when the truth is that OpenAI publishes no balance to read.
        expect(figure(balance({ provider: "openai", status: "unsupported" }))).toBeNull();
    });
});

describe("PILL_TEXT", () => {
    it("names every status a balance can carry", () => {
        // A status with no label renders an empty pill, which reads as a bug.
        const statuses: Balance["status"][] = [
            "ok",
            "low",
            "empty",
            "unsupported",
            "unreachable",
            "unconfigured",
        ];
        for (const status of statuses) {
            expect(PILL_TEXT[status]).toBeTruthy();
        }
    });

    it("does not call a vendor we could not reach empty", () => {
        expect(PILL_TEXT.unreachable).not.toMatch(/empty/i);
        expect(PILL_TEXT.unconfigured).not.toMatch(/empty/i);
    });
});
