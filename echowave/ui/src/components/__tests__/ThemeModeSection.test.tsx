/** Appearance, Buzz's shape: Light / Dark / System, then the theme. */
import { fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mode = vi.hoisted(() => ({ value: "light", set: vi.fn() }));
vi.mock("next-themes", () => ({ useTheme: () => ({ theme: mode.value, setTheme: mode.set }) }));

import { ThemeModeSection } from "../ThemeModeSection";

beforeEach(() => {
    localStorage.clear();
    document.getElementById("decibyl-theme")?.remove();
});

describe("the appearance picker", () => {
    it("marks the stored mode and switches on a click", () => {
        render(<ThemeModeSection />);
        const modes = screen.getByRole("radiogroup", { name: "Mode" });
        expect(within(modes).getByRole("radio", { name: /Light/ }).getAttribute("aria-checked")).toBe("true");
        fireEvent.click(within(modes).getByRole("radio", { name: /Dark/ }));
        expect(mode.set).toHaveBeenCalledWith("dark");
    });

    it("starts on the default theme and applies another on a click", () => {
        render(<ThemeModeSection />);
        const themes = screen.getByRole("radiogroup", { name: "Theme" });
        expect(within(themes).getByRole("radio", { name: /Default/ }).getAttribute("aria-checked")).toBe("true");
        fireEvent.click(within(themes).getByRole("radio", { name: /Solarized/ }));
        expect(localStorage.getItem("decibyl.palette")).toBe("solarized");
        expect(document.getElementById("decibyl-theme")).not.toBeNull();
    });

    it("offers no accent colour swatches", () => {
        render(<ThemeModeSection />);
        expect(screen.queryByRole("radio", { name: "Mauve" })).toBeNull();
        expect(screen.queryByRole("radiogroup", { name: /accent/i })).toBeNull();
    });
});
