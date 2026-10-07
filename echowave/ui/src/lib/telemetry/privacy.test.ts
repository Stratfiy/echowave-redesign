import { describe, expect, it } from "vitest";

import {
    identifyProperties,
    isReplayAllowed,
    REPLAY_ALLOWED_PREFIXES,
    REPLAY_OPTIONS,
    SENSITIVE_SCREENS,
} from "./privacy";

describe("replay policy", () => {
    it("never allows a sensitive screen", () => {
        for (const routes of Object.values(SENSITIVE_SCREENS)) {
            for (const route of routes) {
                expect(isReplayAllowed(route)).toBe(false);
                expect(isReplayAllowed(`${route}/42`)).toBe(false);
            }
        }
    });

    it("lists every sensitive screen the design names", () => {
        const numbers = Object.keys(SENSITIVE_SCREENS).map((k) => k.split(" ")[0]);
        expect(numbers).toEqual([
            "23", "24", "25", "26", "27", "31", "32", "33", "35", "38", "42", "43", "44",
        ]);
    });

    it("allows only the listed everyday screens", () => {
        for (const prefix of REPLAY_ALLOWED_PREFIXES) {
            expect(isReplayAllowed(prefix)).toBe(true);
        }
        expect(isReplayAllowed("/overview/thread/9")).toBe(true);
        expect(isReplayAllowed("/overviewer")).toBe(false);
        expect(isReplayAllowed("/some-new-screen")).toBe(false);
        expect(isReplayAllowed("/auth/login")).toBe(false);
        expect(isReplayAllowed(null)).toBe(false);
    });

    it("masks every input and all text", () => {
        expect(REPLAY_OPTIONS.maskAllInputs).toBe(true);
        expect(REPLAY_OPTIONS.maskTextSelector).toBe("*");
    });
});

describe("identify properties", () => {
    it("drops email and name while redaction is on", () => {
        expect(identifyProperties("asha@example.com", "Asha", true)).toEqual({});
        expect(identifyProperties("asha@example.com", "Asha", false)).toEqual({
            email: "asha@example.com",
            name: "Asha",
        });
        expect(identifyProperties(undefined, null, false)).toEqual({});
    });
});
