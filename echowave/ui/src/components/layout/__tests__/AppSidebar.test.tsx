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
    // The developer doors live in the Setup panel now, so reaching them is two
    // moves: pick the context, then open the folded group inside it.
    expect(screen.queryByRole("button", { name: "DEVELOPERS" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Setup" }));

    expect(screen.queryByRole("link", { name: "API keys & SDKs" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "DEVELOPERS" }));
    expect(screen.getByRole("link", { name: "API keys & SDKs" })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("decibyl.sidebar.closedSections")!)).not.toContain("DEVELOPERS");
  });

  it("offers every context, and opens the one the current page belongs to", () => {
    route.pathname = "/billing";
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    const tabs = screen.getAllByRole("tab").map((t) => t.getAttribute("aria-label"));
    expect(tabs).toEqual(["Home", "Activity", "Marketplace", "Setup", "Account"]);
    // A rail pointing somewhere other than the screen you are reading is
    // worse than no rail.
    expect(screen.getByRole("tab", { name: "Account" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("link", { name: "Billing" })).toBeTruthy();
  });

  it("lets you browse another panel without leaving the page", () => {
    route.pathname = "/billing";
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByRole("tab", { name: "Activity" }).getAttribute("aria-selected")).toBe("true");
    // Campaigns, not Calls: MONITOR is one of the groups folded by default, so
    // asserting on a link inside it would be testing the fold, not the panel.
    expect(screen.getByRole("link", { name: "Campaigns" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Billing" })).toBeNull();
  });
  it("opens a saved closed group when navigating to a related page", () => {
    localStorage.setItem("decibyl.sidebar.closedSections", JSON.stringify(["BUILD"]));
    route.pathname = "/tools/42";
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    expect(screen.getByRole("link", { name: "Integrations" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("button", { name: "BUILD" }).getAttribute("aria-expanded")).toBe("true");
  });
  /* Collapsed is the rail, and nothing may become unreachable from it.
   *
   * The old shape hid the rail and listed all seventeen destinations as bare
   * glyphs in a scrolling column; this assertion is the same guarantee read
   * against the shape that replaced it. Every context is one click away, and
   * the click opens the panel that holds the destinations — so the check is
   * that the doors are there and that using one works, not that seventeen
   * links are crammed into a 56px column. */
  it("offers every context in the collapsed rail", () => {
    render(<SidebarProvider defaultOpen={false}><AppSidebar /></SidebarProvider>);
    for (const title of ["Home", "Activity", "Marketplace", "Setup", "Account"]) {
      expect(screen.getByRole("tab", { name: title })).toBeTruthy();
    }
    // No panel while collapsed: a 56px column cannot hold a destination list.
    expect(screen.queryByRole("link", { name: "Billing" })).toBeNull();
  });
  it("opens the panel when a context is picked from the collapsed rail", () => {
    render(<SidebarProvider defaultOpen={false}><AppSidebar /></SidebarProvider>);
    fireEvent.click(screen.getByRole("tab", { name: "Account" }));
    // Billing, not Settings: WORKSPACE is folded by default, so asserting on
    // a link inside it would be testing the fold rather than the panel.
    expect(screen.getByRole("link", { name: "Billing" })).toBeTruthy();
  });
  it("does not offer staff contexts to a customer", () => {
    render(<SidebarProvider defaultOpen={false}><AppSidebar /></SidebarProvider>);
    fireEvent.click(screen.getByRole("tab", { name: "Account" }));
    expect(screen.queryByRole("link", { name: "Review queue" })).toBeNull();
  });
});
