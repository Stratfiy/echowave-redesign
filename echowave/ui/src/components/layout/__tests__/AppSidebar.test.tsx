import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
    // Setup has no row of its own now -- it is behind the person -- so the
    // panel is reached the way anybody actually gets there: by standing on a
    // page that belongs to it. Knowledge base rather than a developer page,
    // because standing on one of those opens the group being tested.
    route.pathname = "/files";
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);

    expect(screen.queryByRole("link", { name: "API keys & SDKs" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Developers" }));
    expect(screen.getByRole("link", { name: "API keys & SDKs" })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("decibyl.sidebar.closedSections")!)).not.toContain("DEVELOPERS");
  });

  it("offers every context, and opens the one the current page belongs to", () => {
    route.pathname = "/billing";
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    // Two context rows, because four rows are what a day needs and the other
    // three are behind the person.
    const tabs = screen.getAllByRole("tab").map((t) => t.getAttribute("aria-label"));
    expect(tabs).toEqual(["Home", "Activity"]);
    // A rail pointing somewhere other than the screen you are reading is
    // worse than no rail.
    // /billing belongs to Account, which no longer has a row: the panel is
    // still the one showing, and the person at the foot is how you get back
    // to it from elsewhere.
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
    for (const title of ["Home", "Activity"]) {
      expect(screen.getByRole("tab", { name: title })).toBeTruthy();
    }
    // And the person, who carries the other three.
    expect(screen.getByRole("button", { name: "Account menu" })).toBeTruthy();
    // No panel while collapsed: a 56px column cannot hold a destination list.
    expect(screen.queryByRole("link", { name: "Billing" })).toBeNull();
  });
  it("opens the panel when a context is picked from the collapsed rail", () => {
    render(<SidebarProvider defaultOpen={false}><AppSidebar /></SidebarProvider>);
    // Activity, since it is one of the two that still has a row. What this
    // pins is that picking a context from the folded rail opens the panel.
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByRole("link", { name: "Campaigns" })).toBeTruthy();
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
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(router.push).not.toHaveBeenCalled();
  });
  it("the Home panel is channels and bots, not a menu", () => {
    route.pathname = "/overview";
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    // No nav rows in the Home panel: the pinned rows say Home and Bots, the
    // YOUR BOTS section lists them. Bots is a pinned door to the roster, the
    // way Buzz pins Agents above its channels.
    expect(screen.queryByRole("link", { name: "Home" })).toBeNull();
    const bots = screen.getByRole("link", { name: "Bots" });
    expect(bots.getAttribute("href")).toBe("/workflow");
    expect(bots.closest("[data-rail]")).toBeTruthy();
    // The two sections' doors are there even before anything has loaded.
    expect(screen.getByLabelText("New chat")).toBeTruthy();
    expect(screen.getByLabelText("Add a bot")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Knowledge base/ }).getAttribute("href")).toBe("/files");
    // The panel opens on Decibyl, the assistant, above the knowledge base --
    // Slack's Slackbot and Directories. The rail's logo is also named
    // Decibyl, so the row is found inside the panel, not the rail.
    const decibyl = screen
      .getAllByRole("link", { name: "Decibyl" })
      .find((link) => !link.closest("[data-rail]"));
    expect(decibyl?.getAttribute("href")).toBe("/overview");
    // The foot is the person, as Buzz's profile card: the setup call and the
    // account menu sit there, below the list, and stay put when it scrolls.
    const setup = screen.getByRole("link", {
      name: new RegExp(SETUP_CALL_LABEL),
    });
    expect(setup.closest('[data-slot="sidebar-footer"]')).toBeTruthy();
    expect(screen.getByRole("button", { name: "Account menu" }).closest('[data-slot="sidebar-footer"]')).toBeTruthy();
  });
  it("pins the rows in Buzz's order: the places first, then the shop, then the settings", () => {
    render(<SidebarProvider><AppSidebar /></SidebarProvider>);
    const rail = document.querySelector('[role="tablist"][data-rail]')!;
    const rows = Array.from(rail.querySelectorAll("a, button")).map(
      (el) => el.getAttribute("aria-label") ?? el.textContent?.trim(),
    );
    // Four, and only four. The shop, the setup and the account are behind the
    // person: a day needs the thread, what the bots did, the bots, and what
    // runs on its own.
    expect(rows).toEqual(["Home", "Activity", "Bots", "Tasks"]);
  });

  it("does not offer staff contexts to a customer", () => {
    render(<SidebarProvider defaultOpen={false}><AppSidebar /></SidebarProvider>);
    // Every panel a customer can reach, by row or by standing on one of its
    // pages, and the staff queue in none of them.
    for (const context of ["Home", "Activity"]) {
      fireEvent.click(screen.getByRole("tab", { name: context }));
      expect(screen.queryByRole("link", { name: "Review queue" })).toBeNull();
    }
    for (const page of ["/api-keys", "/billing", "/marketplace"]) {
      route.pathname = page;
      cleanup();
      render(<SidebarProvider><AppSidebar /></SidebarProvider>);
      expect(screen.queryByRole("link", { name: "Review queue" }), page).toBeNull();
    }
  });
});
