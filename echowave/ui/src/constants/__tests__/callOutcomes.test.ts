/**
 * The editor's half of the outcome taxonomy.
 *
 * The backend decides what is storable and re-checks everything here, so
 * these tests are about not surprising the person typing: a code they typed
 * one way and see saved another, or a list that quietly drops `unclear` and
 * turns every unreadable call into the nearest real outcome.
 */

import { describe, expect, it } from "vitest";

import {
    DEFAULT_CALL_OUTCOMES,
    normaliseOutcomeCode,
    outcomesToSave,
    UNCLEAR_OUTCOME,
} from "../callOutcomes";

describe("outcome codes", () => {
    it.each([
        ["booked", "booked"],
        ["Call Back", "call_back"],
        ["not-interested", "not_interested"],
        ["  BOOKED  ", "booked"],
        ["wrong number!", "wrong_number"],
    ])("normalises %s the way the backend will", (raw, expected) => {
        expect(normaliseOutcomeCode(raw)).toBe(expected);
    });

    it.each(["", "   ", "123", "_leading", "!!!"])(
        "rejects %s, which cannot be a field name",
        (raw) => {
            expect(normaliseOutcomeCode(raw)).toBeNull();
        },
    );
});

describe("what gets saved", () => {
    it("drops the blank row somebody left mid-thought", () => {
        const saved = outcomesToSave([
            { code: "paid", label: "Paid", when: "" },
            { code: "", label: "", when: "" },
        ]);
        expect(saved.map((o) => o.code)).toEqual(["paid", UNCLEAR_OUTCOME.code]);
    });

    it("always keeps unclear", () => {
        const saved = outcomesToSave([{ code: "paid", label: "Paid", when: "" }]);
        expect(saved.some((o) => o.code === UNCLEAR_OUTCOME.code)).toBe(true);
    });

    it("does not add unclear twice", () => {
        const saved = outcomesToSave([
            { code: "paid", label: "Paid", when: "" },
            { ...UNCLEAR_OUTCOME },
        ]);
        expect(saved.filter((o) => o.code === UNCLEAR_OUTCOME.code)).toHaveLength(1);
    });

    it("collapses a code entered twice", () => {
        const saved = outcomesToSave([
            { code: "paid", label: "Paid", when: "" },
            { code: "Paid", label: "Paid again", when: "" },
        ]);
        expect(saved.filter((o) => o.code === "paid")).toHaveLength(1);
    });

    it("falls back to the code when no label was typed", () => {
        const [first] = outcomesToSave([{ code: "wrong_number", label: "", when: "" }]);
        expect(first.label).toBe("wrong number");
    });

    it("clearing every row saves nothing rather than a list of one", () => {
        // Empty means "use the defaults" to the backend. Saving a lone
        // `unclear` would instead mean every call is unreadable.
        expect(outcomesToSave([{ code: "", label: "", when: "" }])).toEqual([]);
    });

    it("round-trips the defaults unchanged", () => {
        const saved = outcomesToSave([...DEFAULT_CALL_OUTCOMES]);
        expect(saved).toEqual([...DEFAULT_CALL_OUTCOMES]);
    });
});
