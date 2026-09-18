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
  RETIRED_ACCENT_VARIABLES,
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
    // Catppuccin mauve, the hue Buzz leads with in both its modes.
    expect(ACCENTS[0].bright).toBe("#8839ef");
  });

  it("never paints the frame", () => {
    // The rail and the panel are the shell's gradient with dark ink on it.
    // These were written here as inline styles on :root, which beat the
    // stylesheet, so a stored accent kept the sidebar dark long after the
    // gradient shipped. An accent owns the ring, the brand fills and the
    // active row -- nothing that is a surface.
    for (const accent of ACCENTS) {
      const names = Object.keys(accentVariables(accent));
      for (const retired of RETIRED_ACCENT_VARIABLES) {
        expect(names, `${accent.id} still writes ${retired}`).not.toContain(retired);
      }
    }
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
  it("marks the row you are standing on, and nothing wider", () => {
    // What is left of the frame after the gradient: the active row's fill
    // and its label. The eleven surface variables that used to be here are
    // covered by "never paints the frame" above.
    for (const accent of ACCENTS) {
      const vars = accentVariables(accent);
      expect(vars["--sidebar-primary"]).toBe(accent.deep);
      expect(vars["--sidebar-primary-foreground"]).toBe("#ffffff");
      expect(contrastOnWhite(vars["--sidebar-primary"])).toBeGreaterThanOrEqual(
        MIN_DEEP_CONTRAST,
      );
    }
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
    const indigo = ACCENTS.find((accent) => accent.id === "indigo")!;
    const stored = { id: "indigo", vars: accentVariables(indigo) };
    window.localStorage.setItem("decibyl.accent", JSON.stringify(stored));

    eval(ACCENT_BOOT_SCRIPT);
    expect(document.documentElement.style.getPropertyValue("--ring")).toBe("#4f46e5");
    window.localStorage.removeItem("decibyl.accent");
  });

  it("drops a frame colour an older build stored", () => {
    // The shape localStorage still holds on every machine that ran the old
    // build: the accent map with the eleven frame variables in it.
    const indigo = ACCENTS.find((accent) => accent.id === "indigo")!;
    const stored = {
      id: "indigo",
      vars: { ...accentVariables(indigo), "--sidebar": "#341e51", "--rail": "#2f1b19" },
    };
    window.localStorage.setItem("decibyl.accent", JSON.stringify(stored));

    eval(ACCENT_BOOT_SCRIPT);
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--sidebar")).toBe("");
    expect(style.getPropertyValue("--rail")).toBe("");
    expect(style.getPropertyValue("--ring")).toBe("#4f46e5");
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
