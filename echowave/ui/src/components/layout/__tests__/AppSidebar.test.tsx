import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider } from "@/components/ui/sidebar";

import { AppSidebar } from "../AppSidebar";
const route = vi.hoisted(() => ({ pathname: "/overview" }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ provider: "local" }) }));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: null }) }));
vi.mock("@/context/TelephonyConfigWarningsContext", () => ({ useTelephonyConfigWarnings: () => ({}) }));
vi.mock("@/hooks/useAccessRoles", () => ({ useAccessRoles: () => ({ isStaff: false, isOrganizationAdmin: false }) }));
vi.mock("@/hooks/useLatestReleaseVersion", () => ({ useLatestReleaseVersion: () => ({}) }));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => false }));
vi.mock("@/components/layout/SidebarTeamSwitcher", () => ({ SidebarTeamSwitcher: () => null }));
beforeEach(() => {
  localStorage.clear();
  route.pathname = "/overview";
  Element.prototype.scrollIntoView = vi.fn();
  window.matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
});
describe("sidebar interactions", () => {
  it("floats as a rounded card on the floor", () => {
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    expect(document.querySelector('[data-slot="sidebar"]')?.getAttribute("data-variant")).toBe("floating");
  });
  it("lights the sidebar entry a folded tab belongs to", () => {
    route.pathname = "/do-not-call";
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    expect(screen.getByRole("link", { name: "Compliance" }).getAttribute("aria-current")).toBe("page");
    expect(screen.queryByRole("link", { name: "Do not call" })).toBeNull();
  });
  it("expands developers without removing its routes and remembers the choice", () => {
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    expect(screen.queryByRole("link", { name: "API keys & SDKs" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "DEVELOPERS" }));
    expect(screen.getByRole("link", { name: "API keys & SDKs" })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("decibyl.sidebar.closedSections")!)).not.toContain("DEVELOPERS");
  });
  it("opens a saved closed group when navigating to a related page", () => {
    localStorage.setItem("decibyl.sidebar.closedSections", JSON.stringify(["BUILD"]));
    route.pathname = "/tools/42";
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    expect(screen.getByRole("link", { name: "Integrations" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("button", { name: "BUILD" }).getAttribute("aria-expanded")).toBe("true");
  });
  it("keeps all customer destinations accessible in the collapsed rail", () => {
    render(<SidebarProvider defaultOpen={false}><AppSidebar /></SidebarProvider>);
    expect(screen.getByRole("link", { name: "Settings" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Calls" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Compliance" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Review queue" })).toBeNull();
  });
});
