/**
 * The marketplace's filing: nothing dropped, every shelf counted.
 */
import { describe, expect, it } from "vitest";

import {
    type BotTemplate,
    filterBots,
    functions,
    hireHref,
    industries,
    industryOf,
    toneFor,
} from "../marketplace";

const bot = (over: Partial<BotTemplate>): BotTemplate => ({
    id: "x",
    name: "Bot",
    vertical: "Healthcare — clinics",
    industry: "Healthcare",
    function: "Answer calls",
    direction: "inbound",
    summary: "Books appointments.",
    languages: ["en"],
    ...over,
});

const SHELF = [
    bot({ id: "clinic", name: "Clinic front desk" }),
    bot({ id: "resto", name: "Restaurant reservations", industry: "Hospitality", vertical: "Hospitality — restaurants" }),
    bot({ id: "loan", name: "Loan payment reminder", industry: "Lending", function: "Collect payments", direction: "outbound" }),
];

describe("filing", () => {
    it("files a template with no industry under its vertical's first words", () => {
        expect(industryOf({ industry: "", vertical: "Real estate — builders" })).toBe("Real estate");
        expect(industryOf({ industry: "", vertical: "Any business -- filings" })).toBe("Any business");
        expect(industryOf({ industry: "Lending", vertical: "whatever" })).toBe("Lending");
    });

    it("counts each shelf in order of first appearance", () => {
        expect(industries(SHELF)).toEqual([
            { name: "Healthcare", count: 1 },
            { name: "Hospitality", count: 1 },
            { name: "Lending", count: 1 },
        ]);
        expect(functions(SHELF)).toEqual([
            { name: "Answer calls", count: 2 },
            { name: "Collect payments", count: 1 },
        ]);
    });

    it("a bot with no function is filed under Other, not dropped", () => {
        expect(functions([bot({ function: "" })])).toEqual([{ name: "Other", count: 1 }]);
    });
});

describe("filtering", () => {
    it("by industry, by function, and by both", () => {
        expect(filterBots(SHELF, { industry: "Lending" }).map((b) => b.id)).toEqual(["loan"]);
        expect(filterBots(SHELF, { fn: "Answer calls" }).map((b) => b.id)).toEqual(["clinic", "resto"]);
        expect(filterBots(SHELF, { industry: "Healthcare", fn: "Collect payments" })).toEqual([]);
    });

    it("a query matches name, industry, function and summary, case-insensitively", () => {
        expect(filterBots(SHELF, { query: "RESTAURANT" }).map((b) => b.id)).toEqual(["resto"]);
        expect(filterBots(SHELF, { query: "collect" }).map((b) => b.id)).toEqual(["loan"]);
        expect(filterBots(SHELF, { query: "appointments" })).toHaveLength(3);
        expect(filterBots(SHELF, { query: "  " })).toHaveLength(3);
    });
});

describe("the shop front", () => {
    it("a shelf keeps its colour between visits", () => {
        expect(toneFor("Messaging")).toBe(toneFor("Messaging"));
        expect(toneFor("Messaging")).toMatch(/^bg-/);
    });

    it("hiring hands the template to the first-agent flow", () => {
        expect(hireHref("clinic appointment")).toBe("/start?template=clinic%20appointment");
    });
});
