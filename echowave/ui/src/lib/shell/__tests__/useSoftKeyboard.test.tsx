import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useSoftKeyboardOpen } from "../useSoftKeyboard";

function Probe() {
    const open = useSoftKeyboardOpen();
    return (
        <>
            <textarea aria-label="box" />
            <span data-testid="kb">{open ? "open" : "closed"}</span>
        </>
    );
}

function fakeViewport(height: number) {
    const listeners: Array<() => void> = [];
    const viewport = {
        height,
        addEventListener: (_: string, fn: () => void) => listeners.push(fn),
        removeEventListener: () => {},
        fire: () => listeners.forEach((fn) => fn()),
    };
    Object.defineProperty(window, "visualViewport", { value: viewport, configurable: true });
    return viewport;
}

function coarse(matches: boolean) {
    window.matchMedia = vi.fn().mockReturnValue({ matches, addEventListener: () => {}, removeEventListener: () => {} });
}

afterEach(() => {
    Object.defineProperty(window, "visualViewport", { value: undefined, configurable: true });
});

describe("useSoftKeyboardOpen", () => {
    it("is open only when a text field has focus and the visual viewport shrank", () => {
        coarse(true);
        Object.defineProperty(window, "innerHeight", { value: 800, configurable: true });
        const viewport = fakeViewport(800);
        render(<Probe />);
        const box = screen.getByLabelText("box");
        act(() => box.focus());
        expect(screen.getByTestId("kb").textContent).toBe("closed");
        viewport.height = 450;
        act(() => viewport.fire());
        expect(screen.getByTestId("kb").textContent).toBe("open");
    });

    it("never fires on a fine pointer (a laptop has no soft keyboard)", () => {
        coarse(false);
        Object.defineProperty(window, "innerHeight", { value: 800, configurable: true });
        const viewport = fakeViewport(300);
        render(<Probe />);
        act(() => screen.getByLabelText("box").focus());
        act(() => viewport.fire());
        expect(screen.getByTestId("kb").textContent).toBe("closed");
    });
});
