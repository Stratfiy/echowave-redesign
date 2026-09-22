import { describe, expect, it } from "vitest";

import { hirePath, resumePath } from "../hireResume";

describe("carrying a hire across signup", () => {
    it("resumes the hire flow for a real template id", () => {
        expect(resumePath("outbound_prospecting")).toBe("/start?template=outbound_prospecting");
    });
    it("never becomes a way off-site", () => {
        for (const bad of ["//evil.example", "https://evil.example", "/start?template=x&next=//evil", "a b", "", null, undefined]) {
            expect(resumePath(bad as string)).toBeNull();
        }
    });
    it("refuses an id longer than any we make", () => {
        expect(hirePath("x".repeat(65))).toBeNull();
    });
});
