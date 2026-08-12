import { describe, expect, it } from "vitest";

import { detailFromError } from "./apiError";

/**
 * The verification form showed "Field required Field required" — twice, naming
 * neither field. FastAPI had said exactly which two were missing, in each
 * item's `loc`, and this helper dropped it.
 */
describe("detailFromError", () => {
    it("returns a plain string detail as-is", () => {
        expect(detailFromError({ detail: "No organization selected" })).toBe(
            "No organization selected",
        );
    });

    it("names the field a 422 is about", () => {
        expect(
            detailFromError({
                detail: [{ type: "missing", loc: ["body", "legal_name"], msg: "Field required" }],
            }),
        ).toBe("legal_name: Field required");
    });

    it("names every field when several are missing", () => {
        const message = detailFromError({
            detail: [
                { loc: ["body", "business_type"], msg: "Field required" },
                { loc: ["body", "legal_name"], msg: "Field required" },
            ],
        });

        expect(message).toBe("business_type: Field required\nlegal_name: Field required");
    });

    it("drops the wrapper element rather than showing it", () => {
        // "body" is not a field anybody filling in a form has heard of.
        expect(detailFromError({ detail: [{ loc: ["body"], msg: "Field required" }] })).toBe(
            "Field required",
        );
    });

    it("skips array indices, which name nothing the user can see", () => {
        expect(
            detailFromError({
                detail: [{ loc: ["body", "items", 0, "name"], msg: "Field required" }],
            }),
        ).toBe("items.name: Field required");
    });

    it("still prefers an explicit model over a loc", () => {
        expect(
            detailFromError({
                detail: [{ model: "gpt-4o", message: "No rate on file", loc: ["body", "model"] }],
            }),
        ).toBe("gpt-4o: No rate on file");
    });

    it("falls back when there is nothing usable", () => {
        expect(detailFromError({}, "Could not save")).toBe("Could not save");
        expect(detailFromError(undefined, "Could not save")).toBe("Could not save");
        expect(detailFromError({ detail: [] }, "Could not save")).toBe("Could not save");
    });

    it("passes a bare string through", () => {
        expect(detailFromError("Boom")).toBe("Boom");
    });
});
