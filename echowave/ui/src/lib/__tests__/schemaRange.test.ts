import { describe, expect, it } from "vitest";

import { MAX_SLIDER_STOPS, numericSchema, sliderRangeFor } from "@/lib/schemaRange";

/**
 * The bug this fixes: temperature was a bare text box on OpenAI and a slider on
 * MiniMax. Same field, same 0-2 range, two different controls — decided by
 * whether the Python type said `float` or `float | None`, because the nullable
 * form buries the bounds inside an anyOf where nothing was looking.
 */
const OPENAI_TEMPERATURE = {
    anyOf: [{ maximum: 2.0, minimum: 0.0, type: "number" }, { type: "null" }],
    default: null,
};

const MINIMAX_TEMPERATURE = {
    type: "number",
    default: 1.0,
    exclusiveMinimum: 0.0,
    maximum: 2.0,
};

const MAX_TOKENS = {
    anyOf: [{ maximum: 4096, minimum: 16, type: "integer" }, { type: "null" }],
    default: null,
};

describe("looking through the nullable wrapper", () => {
    it("finds the numeric branch of an optional field", () => {
        const found = numericSchema(OPENAI_TEMPERATURE);

        expect(found?.schema.minimum).toBe(0);
        expect(found?.schema.maximum).toBe(2);
        expect(found?.nullable).toBe(true);
    });

    it("leaves a plain number alone", () => {
        expect(numericSchema(MINIMAX_TEMPERATURE)?.nullable).toBe(false);
    });

    it("is not fooled by a field that is not a number at all", () => {
        expect(numericSchema({ type: "string" })).toBeNull();
        expect(numericSchema({ anyOf: [{ type: "string" }, { type: "null" }] })).toBeNull();
        expect(numericSchema(undefined)).toBeNull();
    });
});

describe("which fields become sliders", () => {
    it("optional temperature does, which is the whole point", () => {
        const range = sliderRangeFor(OPENAI_TEMPERATURE);

        expect(range).not.toBeNull();
        expect(range?.min).toBe(0);
        expect(range?.max).toBe(2);
    });

    it("required temperature still does, unchanged", () => {
        expect(sliderRangeFor(MINIMAX_TEMPERATURE)?.max).toBe(2);
    });

    it("max_tokens does not, because 4,080 stops is not a thing to drag", () => {
        expect(sliderRangeFor(MAX_TOKENS)).toBeNull();
    });

    it("an unbounded number does not", () => {
        expect(sliderRangeFor({ type: "number" })).toBeNull();
        expect(sliderRangeFor({ type: "number", minimum: 0 })).toBeNull();
    });

    it("the cutoff is the stated one", () => {
        expect(sliderRangeFor({ type: "number", minimum: 0, maximum: MAX_SLIDER_STOPS })).not.toBeNull();
        expect(sliderRangeFor({ type: "number", minimum: 0, maximum: MAX_SLIDER_STOPS + 1 })).toBeNull();
    });
});

describe("where the handle starts", () => {
    it("an optional field with no default starts mid-range and says it is unset", () => {
        const range = sliderRangeFor(OPENAI_TEMPERATURE);

        // Not the minimum. For temperature, zero is a real and very different
        // setting from "the provider decides", and a handle parked on it would
        // read as a choice somebody made.
        expect(range?.fallback).toBe(1);
        expect(range?.optional).toBe(true);
    });

    it("a field with a real default starts on it and is not flagged", () => {
        const range = sliderRangeFor(MINIMAX_TEMPERATURE);

        expect(range?.fallback).toBe(1);
        expect(range?.optional).toBe(false);
    });
});

describe("bounds a caller cannot be rejected for", () => {
    it("an exclusive minimum starts one step in", () => {
        const range = sliderRangeFor(MINIMAX_TEMPERATURE);

        // MiniMax rejects 0, so the track must not reach it.
        expect(range?.min).toBeGreaterThan(0);
        expect(range?.min).toBe(range!.step);
    });

    it("an inclusive minimum is reachable", () => {
        expect(sliderRangeFor({ type: "number", minimum: 0, maximum: 2 })?.min).toBe(0);
    });
});

describe("step size", () => {
    it("an integral range steps by one", () => {
        expect(sliderRangeFor({ type: "integer", minimum: 0, maximum: 10, default: 5 })?.step).toBe(1);
    });

    it("a narrow fractional range steps finely enough to be worth dragging", () => {
        expect(sliderRangeFor({ type: "number", minimum: 0, maximum: 1 })?.step).toBe(0.05);
        expect(sliderRangeFor({ type: "number", minimum: 0, maximum: 0.5 })?.step).toBe(0.01);
    });
});
