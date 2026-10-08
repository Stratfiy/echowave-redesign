/**
 * The themes (lib/themes.ts), checked rather than trusted.
 *
 * The default's colours live twice -- computed here, written into
 * globals.css so the first paint needs no script -- and the first test fails
 * the moment the two disagree. The rest hold what made the old palette a
 * problem: a colour token with no dark value, text that cannot be read, and
 * the purple coming back.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { applyTheme, DEFAULT_THEME_ID, luminance, THEME_BOOT_SCRIPT, THEME_STYLE_ID, themeById, themeCss, THEMES, themeTokens } from "../themes";

const css = readFileSync(join(__dirname, "..", "..", "app", "globals.css"), "utf8");

function block(selector: string): Record<string, string> {
    const start = css.indexOf(`${selector} {`);
    const text = css.slice(start, css.indexOf("}", start));
    return Object.fromEntries([...text.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}

const contrast = (a: string, b: string) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
};

afterEach(() => {
    document.getElementById(THEME_STYLE_ID)?.remove();
    localStorage.clear();
});

describe("the default theme", () => {
    it("is the design handoff's palette, light and dark", () => {
        // Oct 8: the default left GitHub's greys for the handoff's ink,
        // secondary text, hover grey and hairline. Every token the themes
        // set is still declared, so a picked theme still overrides them all.
        const root = block(":root");
        const dark = block(".dark");
        expect(root["--foreground"]).toBe("#0d0d0d");
        expect(root["--muted-foreground"]).toBe("#5d5d5d");
        expect(root["--accent"]).toBe("#ececec");
        expect(root["--border"]).toBe("rgba(0, 0, 0, 0.1)");
        expect(root["--primary"]).toBe(root["--foreground"]);
        expect(dark["--background"]).toBe("#212121");
        expect(dark["--foreground"]).toBe("#ececec");
        for (const k of Object.keys(themeTokens("github-light", true))) {
            expect(root[k], k).toBeTruthy();
            expect(dark[k], k).toBeTruthy();
        }
    });

    it("has no purple and no accent colour: the primary is the text colour", () => {
        expect(css).not.toMatch(/#8839ef|#7a2fd8|#c6a0f6/i);
        for (const side of ["github-light", "github-dark"]) {
            const t = themeTokens(side, true);
            expect(t["--primary"]).toBe(t["--foreground"]);
            expect(t["--ring"]).toBe(t["--foreground"]);
            expect(t["--brand-blue"]).toBe(t["--foreground"]);
        }
    });

    it("keeps Buzz's sidebar gradient", () => {
        expect(block(":root")["--shell-gradient-top"]).toBe("#e6e6b6");
        expect(block(".dark")["--shell-gradient-bottom"]).toBe("#0a1423");
    });
});

describe("every theme", () => {
    it("sets, in light and dark, every colour token the default does", () => {
        const names = Object.keys(themeTokens("github-light", true)).sort();
        for (const theme of THEMES) {
            expect(Object.keys(themeTokens(theme.light, theme.gradient)).sort(), theme.id).toEqual(names);
            expect(Object.keys(themeTokens(theme.dark, theme.gradient)).sort(), theme.id).toEqual(names);
        }
    });

    it("is readable: body and muted text 4.5:1 on its own background", () => {
        for (const theme of THEMES) {
            for (const side of [theme.light, theme.dark]) {
                const t = themeTokens(side, theme.gradient);
                expect(contrast(t["--foreground"], t["--background"]), side).toBeGreaterThanOrEqual(4.5);
                expect(contrast(t["--muted-foreground"], t["--background"]), side).toBeGreaterThanOrEqual(4.5);
                expect(contrast(t["--primary-foreground"], t["--primary"]), side).toBeGreaterThanOrEqual(4.5);
            }
        }
    });

    it("pairs a light side with a dark one", () => {
        for (const theme of THEMES) {
            expect(luminance(themeTokens(theme.light)["--background"]), theme.id).toBeGreaterThan(0.5);
            expect(luminance(themeTokens(theme.dark)["--background"]), theme.id).toBeLessThan(0.5);
        }
    });
});

describe("choosing a theme", () => {
    it("writes one stylesheet with a light and a dark rule, and remembers it", () => {
        applyTheme(THEMES[1]);
        applyTheme(themeById("solarized"));
        const sheet = document.getElementById(THEME_STYLE_ID)?.textContent ?? "";
        expect(sheet).toContain(":root:not(.dark){");
        expect(sheet).toContain(":root.dark{");
        expect(localStorage.getItem("decibyl.palette")).toBe("solarized");
    });

    it("goes back to the default with no stylesheet at all", () => {
        applyTheme(themeById("solarized"));
        applyTheme(themeById(DEFAULT_THEME_ID));
        expect(document.getElementById(THEME_STYLE_ID)).toBeNull();
        expect(localStorage.getItem("decibyl.palette")).toBeNull();
        expect(themeCss(themeById(DEFAULT_THEME_ID))).toBe("");
    });

    it("clears the accent colour an older build stored, inline and in storage", () => {
        localStorage.setItem("decibyl.accent", JSON.stringify({ id: "mauve" }));
        document.documentElement.style.setProperty("--brand-blue", "#7a2fd8");
        applyTheme(themeById(DEFAULT_THEME_ID), { store: false });
        expect(document.documentElement.style.getPropertyValue("--brand-blue")).toBe("");
        expect(localStorage.getItem("decibyl.accent")).toBeNull();
    });

    it("an unknown stored theme falls back to the default", () => {
        expect(themeById("no-such-theme").id).toBe(DEFAULT_THEME_ID);
    });
});

describe("the boot script", () => {
    it("restores a chosen theme before first paint, and clears the old accent", () => {
        localStorage.setItem("decibyl.palette", "gruvbox");
        localStorage.setItem("decibyl.accent", "{}");
        document.documentElement.style.setProperty("--ring", "#8839ef");
        eval(THEME_BOOT_SCRIPT);
        expect(document.getElementById(THEME_STYLE_ID)?.textContent).toBe(themeCss(themeById("gruvbox")));
        expect(document.documentElement.style.getPropertyValue("--ring")).toBe("");
        expect(localStorage.getItem("decibyl.accent")).toBeNull();
    });

    it("does nothing for the default or a junk value", () => {
        for (const v of [null, "default", "junk"]) {
            if (v) localStorage.setItem("decibyl.palette", v);
            else localStorage.removeItem("decibyl.palette");
            expect(() => eval(THEME_BOOT_SCRIPT)).not.toThrow();
            expect(document.getElementById(THEME_STYLE_ID)).toBeNull();
        }
    });
});
