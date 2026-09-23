/**
 * Every colour a screen reads has a dark value.
 *
 * Dark mode was switched off for months because a second palette nobody
 * checked is how a screen ships with unreadable text. The palette is back
 * (Settings → Appearance), so the check is here instead: a colour token added
 * to :root without a .dark value fails this, rather than showing a pale wash
 * with pale text on it in dark mode, which is exactly what the verify-email
 * banner and the Marketplace hero did before this test existed.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { accentDarkVariables, ACCENTS,accentVariables } from "@/lib/accent";

const css = readFileSync(join(__dirname, "..", "globals.css"), "utf8");

function block(selector: string): string {
    const start = css.indexOf(`${selector} {`);
    expect(start, selector).toBeGreaterThan(-1);
    return css.slice(start, css.indexOf("}", start));
}

const tokens = (text: string) => [...text.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => ({ name: m[1], value: m[2] }));

/** Colours that read the same on either ground, or that nothing paints with. */
const SAME_IN_BOTH = new Set([
    // Named palette, kept for Tailwind's colour scale; no screen uses them.
    "--color-canvas-white", "--color-paper-mist", "--color-ash", "--color-smoke", "--color-pebble",
    "--color-midnight-ink", "--color-charcoal", "--color-graphite", "--color-slate", "--color-steel",
    "--color-fog", "--color-silver", "--color-tangerine", "--color-lavender", "--color-vivid-green",
    // Brand and chart hues: fills and marks, never a surface or text colour.
    "--brand-gradient", "--brand-pink", "--brand-amber",
    "--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5",
]);

describe("the dark palette", () => {
    it("gives every colour token on :root a dark value, or says why not", () => {
        const dark = new Set(tokens(block(".dark")).map((t) => t.name));
        const accentOwned = new Set(Object.keys(accentVariables(ACCENTS[0])));
        const missing = tokens(block(":root"))
            .filter((t) => /#[0-9a-f]{3,8}\b|rgba?\(/i.test(t.value) && !t.name.startsWith("--shadow"))
            .map((t) => t.name)
            .filter((name) => !dark.has(name) && !accentOwned.has(name) && !SAME_IN_BOTH.has(name));
        expect(missing).toEqual([]);
    });

    it("has the accent cover, in dark, every token it sets in light", () => {
        for (const accent of ACCENTS) {
            expect(Object.keys(accentDarkVariables(accent)).sort(), accent.id).toEqual(Object.keys(accentVariables(accent)).sort());
        }
    });
});
