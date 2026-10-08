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
    lifeStageOf,
    lifeStages,
    toneFor,
} from "../marketplace";

const bot = (over: Partial<BotTemplate>): BotTemplate => ({
    id: "x",
    name: "Agent",
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

    it("an agent with no function is filed under Other, not dropped", () => {
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

describe("life stages", () => {
    const STAGED = [
        ...SHELF,
        bot({ id: "money", name: "Money Chaser", life_stage: "small_business", life_stage_label: "Small business" }),
        bot({ id: "scam", name: "Scam Shield", life_stage: "seniors", life_stage_label: "Seniors (60+)" }),
        bot({ id: "pills", name: "Medicine Caller", life_stage: "seniors", life_stage_label: "Seniors (60+)" }),
        bot({ id: "odd", name: "Odd", life_stage: "toddlers", life_stage_label: null }),
    ];

    it("files by stage in the catalogue's order, counting only staged bots", () => {
        expect(lifeStages(STAGED)).toEqual([
            { name: "Small business", count: 1 },
            { name: "Seniors (60+)", count: 2 },
            // A stage with no heading still shows, under its key.
            { name: "toddlers", count: 1 },
        ]);
        expect(lifeStages(SHELF)).toEqual([]);
        expect(lifeStageOf(SHELF[0])).toBeNull();
    });

    it("filters by stage, alone and with the other filters", () => {
        expect(filterBots(STAGED, { stage: "Seniors (60+)" }).map((b) => b.id)).toEqual(["scam", "pills"]);
        expect(filterBots(STAGED, { stage: "Seniors (60+)", query: "scam" }).map((b) => b.id)).toEqual(["scam"]);
        expect(filterBots(STAGED, { query: "small business" }).map((b) => b.id)).toEqual(["money"]);
    });
});
