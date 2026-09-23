/**
 * The accent picker, from the outside: pick one, it takes; pick a pale one,
 * it refuses and says why.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { AccentSection } from "../AccentSection";

beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("style");
});

describe("the accent picker", () => {
    it("opens on the accent the app is already wearing", () => {
        render(<AccentSection />);
        expect(screen.getByRole("radio", { name: "Mauve" }).getAttribute("aria-checked")).toBe("true");
    });

    it("applies a chosen accent to the document and remembers it", () => {
        render(<AccentSection />);
        fireEvent.click(screen.getByRole("radio", { name: "Indigo" }));

        // Written as a stylesheet with a light and a dark rule, never inline:
        // inline beat the dark palette.
        const sheet = document.getElementById("decibyl-accent")?.textContent ?? "";
        expect(sheet).toContain("--ring:#4f46e5");
        // The token that carries text gets the deep half, never the bright one.
        expect(sheet).toContain("--brand-blue:#4338ca");
        expect(sheet).toContain(":root.dark{");
        expect(document.documentElement.style.getPropertyValue("--ring")).toBe("");

        const stored = JSON.parse(window.localStorage.getItem("decibyl.accent") ?? "{}");
        expect(stored.id).toBe("indigo");
        expect(stored.vars["--ring"]).toBe("#4f46e5");
        expect(stored.css).toContain(":root.dark{");
    });

    it("offers the presets and no custom picker", () => {
        render(<AccentSection />);
        expect(screen.getAllByRole("radio")).toHaveLength(9);
        expect(screen.queryByLabelText(/pick your own/i)).toBeNull();
    });

    it("restores a stored accent on mount", () => {
        window.localStorage.setItem("decibyl.accent", JSON.stringify({ id: "rose", vars: {} }));
        render(<AccentSection />);
        expect(screen.getByRole("radio", { name: "Rose" }).getAttribute("aria-checked")).toBe("true");
    });
});
