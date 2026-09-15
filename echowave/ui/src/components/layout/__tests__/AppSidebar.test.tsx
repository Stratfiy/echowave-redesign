import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider } from "@/components/ui/sidebar";
import { SETUP_CALL_LABEL } from "@/constants/setupCall";

import { AppSidebar } from "../AppSidebar";
const route = vi.hoisted(() => ({ pathname: "/overview" }));
const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname, useRouter: () => router }));
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
  it("is flush with the edge, the full height, like Slack's", () => {
    // It floated as a rounded card for a while, which put a white header
    // above a dark rail and a card of its own colour under it.
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    expect(document.querySelector('[data-slot="sidebar"]')?.getAttribute("data-variant")).toBe("sidebar");
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
    expect(screen.queryByRole("button", { name: "Developers" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Setup" }));

    expect(screen.queryByRole("link", { name: "API keys & SDKs" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Developers" }));
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
    localStorage.setItem("decibyl.sidebar.closedSections", JSON.stringify(["DEPLOY"]));
    route.pathname = "/numbers";
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    expect(screen.getByRole("link", { name: "Phone numbers" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("button", { name: "Deploy" }).getAttribute("aria-expanded")).toBe("true");
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
  it("the rail's Home button goes home, since the panel no longer lists it", () => {
    /* The Home row was removed from the panel so the panel could be the
       workspace -- channels and bots -- rather than a menu with "Home" in it
       twice. That leaves the rail button as the only way to /overview from
       the sidebar, so it navigates. The other contexts stay browse-only:
       looking at what is in Setup without leaving the page you are on is a
       thing people do. */
    route.pathname = "/billing";
    router.push.mockReset();
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    fireEvent.click(screen.getByRole("tab", { name: "Home" }));
    expect(router.push).toHaveBeenCalledWith("/overview");
    router.push.mockReset();
    fireEvent.click(screen.getByRole("tab", { name: "Setup" }));
    expect(router.push).not.toHaveBeenCalled();
  });
  it("the Home panel is channels and bots, not a menu", () => {
    route.pathname = "/overview";
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    // No nav rows in the Home panel: the rail says Home, YOUR BOTS says bots.
    expect(screen.queryByRole("link", { name: "Home" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Bots" })).toBeNull();
    // The two sections' doors are there even before anything has loaded.
    expect(screen.getByLabelText("New chat")).toBeTruthy();
    expect(screen.getByLabelText("Add a bot")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Company knowledge/ }).getAttribute("href")).toBe("/files");
    // The panel opens on Decibyl, the assistant, above Company knowledge --
    // Slack's Slackbot and Directories. The rail's logo is also named
    // Decibyl, so the row is found inside the panel, not the rail.
    const decibyl = screen
      .getAllByRole("link", { name: "Decibyl" })
      .find((link) => !link.closest("[data-rail]"));
    expect(decibyl?.getAttribute("href")).toBe("/overview");
    // The setup call sits on the rail, under Account, not in a foot of the
    // panel: the panel ends where its list ends.
    const setup = screen.getByRole("link", {
      name: new RegExp(SETUP_CALL_LABEL),
    });
    expect(setup.closest("[data-rail]")).toBeTruthy();
    expect(document.querySelector('[data-slot="sidebar-footer"]')).toBeNull();
  });
  it("does not offer staff contexts to a customer", () => {
    render(<SidebarProvider defaultOpen={false}><AppSidebar /></SidebarProvider>);
    fireEvent.click(screen.getByRole("tab", { name: "Account" }));
    expect(screen.queryByRole("link", { name: "Review queue" })).toBeNull();
  });
});
