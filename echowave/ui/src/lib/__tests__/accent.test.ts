/**
 * The accent palette, checked rather than asserted.
 *
 * Every number here is re-derived from the hex values in accent.ts. A comment
 * claiming a colour is accessible is worth nothing the day somebody edits the
 * hex and not the comment.
 */

import { describe, expect, it } from "vitest";

import {
  ACCENT_BOOT_SCRIPT,
  ACCENTS,
  accentVariables,
  contrastOnWhite,
  deepen,
  DEFAULT_ACCENT_ID,
  MIN_BRIGHT_CONTRAST,
  MIN_DEEP_CONTRAST,
  resolveAccent,
} from "../accent";

describe("the accent palette", () => {
  it("gives every preset a bright half that can hold a focus ring", () => {
    for (const accent of ACCENTS) {
      expect(
        contrastOnWhite(accent.bright),
        `${accent.label} (${accent.bright}) is too pale for a focus ring`,
      ).toBeGreaterThanOrEqual(MIN_BRIGHT_CONTRAST);
    }
  });

  it("gives every preset a deep half that can be read as text", () => {
    for (const accent of ACCENTS) {
      expect(
        contrastOnWhite(accent.deep),
        `${accent.label} (${accent.deep}) fails as link text on white`,
      ).toBeGreaterThanOrEqual(MIN_DEEP_CONTRAST);
    }
  });

  it("starts on the colour the app already ships, so settings match the screen", () => {
    expect(ACCENTS[0].id).toBe(DEFAULT_ACCENT_ID);
    expect(ACCENTS[0].bright).toBe("#e15b53");
  });

  it("has no duplicate ids", () => {
    expect(new Set(ACCENTS.map((a) => a.id)).size).toBe(ACCENTS.length);
  });
});

describe("deepen", () => {
  it("darkens any colour until it is readable on white", () => {
    for (const start of ["#ffff00", "#7dd3fc", "#e15b35", "#000000", "#84cc16"]) {
      expect(contrastOnWhite(deepen(start))).toBeGreaterThanOrEqual(MIN_DEEP_CONTRAST);
    }
  });

  it("leaves a colour that already passes alone", () => {
    expect(deepen("#b24747")).toBe("#b24747");
  });
});

describe("resolveAccent", () => {
  it("falls back to the default for nothing, junk, and a removed preset", () => {
    for (const stored of [null, undefined, "", "not-a-colour", "#zzz", "chartreuse-2"]) {
      expect(resolveAccent(stored).id).toBe(DEFAULT_ACCENT_ID);
    }
  });

  it("no longer takes a custom hex: a theme is presets only", () => {
    expect(resolveAccent("#0d9488").id).toBe(DEFAULT_ACCENT_ID);
    expect(resolveAccent("#fff9c4").id).toBe(DEFAULT_ACCENT_ID);
  });

  it("resolves a known preset by id", () => {
    expect(resolveAccent("indigo").bright).toBe("#4f46e5");
  });
});

describe("accentVariables", () => {
  it("themes the frame: a dark rail, a tinted panel, the deep half on the rail's active state", () => {
    const vars = accentVariables(ACCENTS[2]); // indigo
    expect(vars["--rail"]).toMatch(/^#[0-9a-f]{6}$/);
    expect(contrastOnWhite(vars["--rail"])).toBeGreaterThan(10);
    expect(contrastOnWhite(vars["--sidebar"])).toBeLessThan(1.3);
    expect(vars["--rail-accent"]).toBe(ACCENTS[2].deep);
  });

  it("never touches the button tokens", () => {
    // Every pressable fill is ink. An accent that could repaint --primary
    // would let a pale choice produce an unreadable button.
    const names = Object.keys(accentVariables(ACCENTS[1]));
    for (const forbidden of ["--primary", "--cta", "--foreground", "--background"]) {
      expect(names).not.toContain(forbidden);
    }
  });

  it("moves the focus ring with the accent", () => {
    const vars = accentVariables(ACCENTS[2]);
    expect(vars["--ring"]).toBe(ACCENTS[2].bright);
    expect(vars["--sidebar-ring"]).toBe(ACCENTS[2].bright);
  });

  it("puts the deep half, not the bright one, on the token that carries text", () => {
    const vars = accentVariables(ACCENTS[1]);
    expect(vars["--brand-blue"]).toBe(ACCENTS[1].deep);
    expect(contrastOnWhite(vars["--brand-blue"])).toBeGreaterThanOrEqual(MIN_DEEP_CONTRAST);
  });

  it("emits every variable as a custom property", () => {
    for (const name of Object.keys(accentVariables(ACCENTS[0]))) {
      expect(name.startsWith("--")).toBe(true);
    }
  });
});

describe("the anti-flash boot script", () => {
  it("applies stored variables to the document", () => {
    const stored = { id: "indigo", vars: accentVariables(ACCENTS[2]) };
    window.localStorage.setItem("decibyl.accent", JSON.stringify(stored));

    eval(ACCENT_BOOT_SCRIPT);
    expect(document.documentElement.style.getPropertyValue("--ring")).toBe("#4f46e5");
    window.localStorage.removeItem("decibyl.accent");
  });

  it("does nothing at all when storage is empty or malformed", () => {
    document.documentElement.style.removeProperty("--ring");
    for (const raw of ["", "coral", "{", '{"id":"indigo"}']) {
      if (raw) window.localStorage.setItem("decibyl.accent", raw);
      else window.localStorage.removeItem("decibyl.accent");

      expect(() => eval(ACCENT_BOOT_SCRIPT)).not.toThrow();
      expect(document.documentElement.style.getPropertyValue("--ring")).toBe("");
    }
    window.localStorage.removeItem("decibyl.accent");
  });
});
