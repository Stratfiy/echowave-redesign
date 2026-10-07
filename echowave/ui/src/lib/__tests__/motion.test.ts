import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { duration, MOTION, REDUCED_MOTION, scrollBehavior } from "../motion";

const css = readFileSync(join(process.cwd(), "src/app/motion.css"), "utf8");

const CSS_NAME: Record<keyof typeof MOTION, string> = {
    m1Hover: "--motion-m1-hover",
    m1Press: "--motion-m1-press",
    m2: "--motion-m2",
    m3Enter: "--motion-m3-enter",
    m3Exit: "--motion-m3-exit",
    m4Enter: "--motion-m4-enter",
    m4Exit: "--motion-m4-exit",
    m5: "--motion-m5",
    m6: "--motion-m6",
    m8: "--motion-m8",
};

function declared(block: string, name: string): number | null {
    const match = new RegExp(`${name}:\\s*(\\d+)ms`).exec(block);
    return match ? Number(match[1]) : null;
}

describe("motion tokens", () => {
    const [base, reduced] = css.split("@media (prefers-reduced-motion: reduce)");

    it("match the handoff's numbers in CSS and in code", () => {
        expect(MOTION).toMatchObject({ m1Hover: 120, m1Press: 90, m2: 200, m3Enter: 240, m3Exit: 180, m4Enter: 200, m4Exit: 150, m6: 180, m8: 120 });
        for (const [token, name] of Object.entries(CSS_NAME)) {
            expect(declared(base, name), name).toBe(MOTION[token as keyof typeof MOTION]);
        }
    });

    it("drop travel and continuous effects, and cap fades at 100ms, under reduced motion", () => {
        for (const [token, name] of Object.entries(CSS_NAME)) {
            const value = declared(reduced, name);
            expect(value, name).toBe(REDUCED_MOTION[token as keyof typeof MOTION]);
            expect(value!).toBeLessThanOrEqual(100);
        }
        expect(reduced).toContain("--motion-m3-travel: 0px");
        expect(reduced).toContain("--motion-m6-travel: 0px");
        expect(reduced).toMatch(/\.motion-continuous\s*{\s*animation: none/);
        expect(scrollBehavior(true)).toBe("auto");
        expect(duration("m3Enter", true)).toBe(100);
        expect(duration("m3Enter", false)).toBe(240);
    });

    it("never animates every property", () => {
        const rules = css.replace(/\/\*[\s\S]*?\*\//g, "");
        expect(rules).not.toMatch(/transition:\s*all/);
        expect(rules).not.toMatch(/transition-property:\s*all/);
    });
});
