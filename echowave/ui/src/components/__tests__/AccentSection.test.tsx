/**
 * The accent picker, from the outside: pick one, it takes; pick a pale one,
 * it refuses and says why.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { contrastOnWhite, MIN_DEEP_CONTRAST } from "@/lib/accent";

import { AccentSection } from "../AccentSection";

beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("style");
});

describe("the accent picker", () => {
    it("opens on the accent the app is already wearing", () => {
        render(<AccentSection />);
        expect(screen.getByRole("radio", { name: "Coral" }).getAttribute("aria-checked")).toBe("true");
    });

    it("applies a chosen accent to the document and remembers it", () => {
        render(<AccentSection />);
        fireEvent.click(screen.getByRole("radio", { name: "Indigo" }));

        expect(document.documentElement.style.getPropertyValue("--ring")).toBe("#4f46e5");
        // The token that carries text gets the deep half, never the bright one.
        expect(document.documentElement.style.getPropertyValue("--brand-blue")).toBe("#4338ca");

        const stored = JSON.parse(window.localStorage.getItem("decibyl.accent") ?? "{}");
        expect(stored.id).toBe("indigo");
        expect(stored.vars["--ring"]).toBe("#4f46e5");
    });

    it("refuses a custom colour too pale to hold a focus ring, and keeps the old one", () => {
        render(<AccentSection />);
        fireEvent.click(screen.getByRole("radio", { name: "Teal" }));
        expect(document.documentElement.style.getPropertyValue("--ring")).toBe("#0d9488");

        fireEvent.change(screen.getByLabelText(/pick your own/i), { target: { value: "#fff9c4" } });

        expect(screen.getByRole("alert").textContent).toMatch(/too pale to hold a focus ring/i);
        // The point of the refusal: the ring that was there is still there.
        expect(document.documentElement.style.getPropertyValue("--ring")).toBe("#0d9488");
        expect(screen.getByRole("radio", { name: "Teal" }).getAttribute("aria-checked")).toBe("true");
    });

    it("accepts a custom colour that passes and derives a readable deep half", () => {
        render(<AccentSection />);
        // #1f9d55 scores 3.49 on white: bright enough for a ring, too light to
        // read as text. That gap is the whole reason an accent is a pair.
        fireEvent.change(screen.getByLabelText(/pick your own/i), { target: { value: "#1f9d55" } });

        expect(screen.queryByRole("alert")).toBeNull();
        expect(document.documentElement.style.getPropertyValue("--ring")).toBe("#1f9d55");

        const deep = document.documentElement.style.getPropertyValue("--brand-blue");
        expect(deep).not.toBe("#1f9d55");
        expect(contrastOnWhite(deep)).toBeGreaterThanOrEqual(MIN_DEEP_CONTRAST);
    });

    it("leaves a custom colour alone when it is already readable as text", () => {
        render(<AccentSection />);
        // #2563eb is 5.17 on white, so there is nothing to darken.
        fireEvent.change(screen.getByLabelText(/pick your own/i), { target: { value: "#2563eb" } });
        expect(document.documentElement.style.getPropertyValue("--brand-blue")).toBe("#2563eb");
    });

    it("restores a stored accent on mount", () => {
        window.localStorage.setItem("decibyl.accent", JSON.stringify({ id: "rose", vars: {} }));
        render(<AccentSection />);
        expect(screen.getByRole("radio", { name: "Rose" }).getAttribute("aria-checked")).toBe("true");
    });
});
