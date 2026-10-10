/**
 * The profile menu as the phone header opens it (shell_mobile): Settings,
 * Agents and the advanced tools stay reachable for whoever may use them,
 * and the staff tools appear only for staff.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const roles = vi.hoisted(() => ({ isStaff: false, isOrganizationAdmin: true, staffRole: null as string | null }));
vi.mock("next/navigation", () => ({ usePathname: () => "/overview" }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { displayName: "Asha Rao" }, logout: vi.fn(), provider: "local" }) }));
vi.mock("@/hooks/useAccessRoles", () => ({ useAccessRoles: () => roles }));
vi.mock("@/lib/features", () => ({ useFeature: () => false }));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: {} }) }));
vi.mock("@/hooks/useLatestReleaseVersion", () => ({ useLatestReleaseVersion: () => ({ isBehind: false }) }));
vi.mock("@/components/layout/OrganizationSwitcher", () => ({ OrganizationSwitcher: () => null }));
vi.mock("@/components/layout/SidebarTeamSwitcher", () => ({ SidebarTeamSwitcher: () => null }));
vi.mock("../useRailData", () => ({ useRailData: () => ({ colleagues: [] }) }));
vi.mock("../RecentsList", () => ({ RecentsList: () => null }));
vi.mock("@/components/ui/sidebar", () => ({
    Sidebar: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    SidebarTrigger: () => null,
    useSidebar: () => ({ state: "expanded", isMobile: true, setOpenMobile: vi.fn() }),
}));

import { AccountMenu } from "../AppRailV2";

beforeEach(() => {
    roles.isStaff = false;
    roles.staffRole = null;
});

async function open() {
    render(<AccountMenu collapsed={false} onNavigate={vi.fn()} side="bottom" compact />);
    const trigger = screen.getByRole("button", { name: "Profile and settings" });
    expect(trigger.className).toContain("h-11");
    expect(trigger.textContent).toBe("AR");
    fireEvent.keyDown(trigger, { key: "Enter" });
    return screen.findByRole("menuitem", { name: /Settings/ });
}

describe("the profile menu on a phone", () => {
    it("reaches Settings, Agents and the workspace's tools", async () => {
        const settings = await open();
        expect(settings.getAttribute("href")).toBe("/settings");
        expect(screen.getByRole("menuitem", { name: /Agents/ }).getAttribute("href")).toBe("/workflow");
        expect(screen.getByRole("menuitem", { name: /Sign out/ })).toBeTruthy();
        expect(screen.queryByText("Staff")).toBeNull();
    });

    it("shows staff tools only to staff", async () => {
        roles.isStaff = true;
        roles.staffRole = "superadmin";
        await open();
        expect(screen.getByText("Staff")).toBeTruthy();
    });
});
