import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it } from "vitest";

import {
    AUX_DEFAULT_WIDTH_PX,
    AUX_MIN_WIDTH_PX,
    AUX_SINGLE_PANE_BELOW_PX,
    AuxiliaryPanel,
    auxMaxWidth,
    clampAuxWidth,
} from "../AuxiliaryPanel";

function widen(px: number) {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: px, writable: true });
    window.dispatchEvent(new Event("resize"));
}

beforeEach(() => {
    window.localStorage.clear();
    widen(1440);
});

describe("the panel never squeezes the pane beside it", () => {
    it("keeps the main pane its minimum, however wide the drag", () => {
        expect(clampAuxWidth(9000, 1000)).toBe(1000 - AUX_MIN_WIDTH_PX);
        expect(auxMaxWidth(1000)).toBe(1000 - AUX_MIN_WIDTH_PX);
    });

    it("never narrows past its own minimum", () => {
        expect(clampAuxWidth(10, 1440)).toBe(AUX_MIN_WIDTH_PX);
    });

    it("lets the panel grow on a wide screen rather than capping it short", () => {
        // A static cap is too small on an ultrawide, so the ceiling follows
        // the viewport and the static one is the floor.
        expect(auxMaxWidth(3000)).toBe(3000 - AUX_MIN_WIDTH_PX);
        expect(auxMaxWidth(500)).toBe(AUX_DEFAULT_WIDTH_PX);
    });
});

describe("the panel beside the thread", () => {
    it("opens at its default width and can be dragged", () => {
        render(<AuxiliaryPanel label="About this agent">body</AuxiliaryPanel>);
        const panel = screen.getByTestId("auxiliary-panel");
        expect(panel.style.width).toBe(`${AUX_DEFAULT_WIDTH_PX}px`);
        expect(screen.getByTestId("auxiliary-panel-resize")).toBeTruthy();
    });

    it("reopens at the width the reader last chose", () => {
        window.localStorage.setItem("decibyl.auxiliaryPanel.width", "520");
        render(<AuxiliaryPanel label="About this agent">body</AuxiliaryPanel>);
        expect(screen.getByTestId("auxiliary-panel").style.width).toBe("520px");
    });

    it("takes the screen where there is no room for two panes", () => {
        // Not a device test: below twice the minimum there is nowhere for the
        // thread to sit beside it, so a strip would be two screens on one.
        widen(AUX_SINGLE_PANE_BELOW_PX - 1);
        render(<AuxiliaryPanel label="About this agent">body</AuxiliaryPanel>);
        const panel = screen.getByTestId("auxiliary-panel");
        expect(panel.className).toContain("fixed");
        expect(panel.style.width).toBe("");
        // Nothing to drag when it is the whole screen.
        expect(screen.queryByTestId("auxiliary-panel-resize")).toBeNull();
    });

    it("heads itself, so a tenant never draws a second bar", () => {
        // About and the tester each had their own title-and-close row. The
        // panel owns it now: the control that puts the panel away sits beside
        // the title, as Refero's reference has it, and the tenant's own
        // action sits opposite.
        render(
            <AuxiliaryPanel
                label="About this agent"
                onClose={() => {}}
                action={<button type="button">Edit</button>}
            >
                body
            </AuxiliaryPanel>,
        );
        expect(screen.getByText("About this agent")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Close About this agent" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
    });

    it("has no close control when there is nothing to close to", () => {
        render(<AuxiliaryPanel label="Test this agent">body</AuxiliaryPanel>);
        expect(screen.queryByRole("button", { name: /^Close/ })).toBeNull();
        expect(screen.getByText("Test this agent")).toBeTruthy();
    });

    it("survives storage it cannot read", () => {
        window.localStorage.setItem("decibyl.auxiliaryPanel.width", "not-a-number");
        render(<AuxiliaryPanel label="About this agent">body</AuxiliaryPanel>);
        expect(screen.getByTestId("auxiliary-panel").style.width).toBe(`${AUX_DEFAULT_WIDTH_PX}px`);
    });
});
