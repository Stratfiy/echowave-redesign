/** Light, dark or the system's choice, as three previews (Buzz's shape). */
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

const theme = vi.hoisted(() => ({ value: "light", set: vi.fn() }));
vi.mock("next-themes", () => ({ useTheme: () => ({ theme: theme.value, setTheme: theme.set }) }));

import { ThemeModeSection } from "../ThemeModeSection";

describe("the appearance picker", () => {
    it("marks the stored choice and switches on a click", () => {
        render(<ThemeModeSection />);
        expect(screen.getByRole("radio", { name: /Light/ }).getAttribute("aria-checked")).toBe("true");
        fireEvent.click(screen.getByRole("radio", { name: /Dark/ }));
        expect(theme.set).toHaveBeenCalledWith("dark");
        fireEvent.click(screen.getByRole("radio", { name: /System/ }));
        expect(theme.set).toHaveBeenCalledWith("system");
    });
});
