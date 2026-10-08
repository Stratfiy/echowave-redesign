/**
 * The phone shell under `shell_mobile`: Chat and Today at the bottom (no
 * Menu), the drawer and the profile in the header, the bar gone while the
 * keyboard is up, 44px targets. With the flag off the bar is what it was.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
    flags: {} as Record<string, boolean>,
    keyboard: false,
    toggle: vi.fn(),
    setOpenMobile: vi.fn(),
    pathname: "/overview",
}));

vi.mock("next/navigation", () => ({ usePathname: () => state.pathname }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(state.flags[name]) }));
vi.mock("@/lib/shell/useSoftKeyboard", () => ({ useSoftKeyboardOpen: () => state.keyboard }));
vi.mock("@/components/ui/sidebar", () => ({
    useSidebar: () => ({ toggleSidebar: state.toggle, openMobile: false, setOpenMobile: state.setOpenMobile }),
}));
vi.mock("../AppRailV2", () => ({
    AccountMenu: (props: { side?: string; compact?: boolean }) => (
        <button type="button" data-side={props.side} data-compact={String(props.compact)}>
            Profile and settings
        </button>
    ),
}));

import { MobileHeader } from "../MobileHeader";
import { MobileTabBar } from "../MobileTabBar";

beforeEach(() => {
    state.flags = {};
    state.keyboard = false;
    state.toggle.mockReset();
    state.pathname = "/overview";
});

describe("the bottom bar", () => {
    it("with the flag off keeps Chat, Today and Menu", () => {
        render(<MobileTabBar />);
        expect(screen.getByRole("link", { name: "Chat" })).toBeTruthy();
        expect(screen.getByRole("link", { name: "Today" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Menu" })).toBeTruthy();
    });

    it("with shell_mobile is Chat and Today only, at 44px", () => {
        state.flags = { shell_mobile: true };
        render(<MobileTabBar />);
        expect(screen.queryByRole("button", { name: "Menu" })).toBeNull();
        const chat = screen.getByRole("link", { name: "Chat" });
        expect(chat.getAttribute("aria-current")).toBe("page");
        expect(chat.className).toContain("min-h-11");
        expect(screen.getByTestId("mobile-tab-bar").className).toContain("safe-area-inset-bottom");
    });

    it("steps aside while the software keyboard is open", () => {
        state.flags = { shell_mobile: true };
        state.keyboard = true;
        render(<MobileTabBar />);
        expect(screen.queryByTestId("mobile-tab-bar")).toBeNull();
    });

    it("lights Today on its tabs", () => {
        state.flags = { shell_mobile: true };
        state.pathname = "/schedules";
        render(<MobileTabBar />);
        expect(screen.getByRole("link", { name: "Today" }).getAttribute("aria-current")).toBe("page");
    });
});

describe("the phone header", () => {
    it("holds the drawer and the profile, opening downward", () => {
        render(<MobileHeader />);
        const menu = screen.getByRole("button", { name: /Open navigation/ });
        expect(menu.className).toContain("h-11");
        fireEvent.click(menu);
        expect(state.toggle).toHaveBeenCalledOnce();
        const profile = screen.getByText("Profile and settings");
        expect(profile.getAttribute("data-side")).toBe("bottom");
        expect(profile.getAttribute("data-compact")).toBe("true");
        expect(screen.getByText("Chat")).toBeTruthy();
        expect(screen.getByTestId("mobile-header").className).toContain("safe-area-inset-top");
    });
});
