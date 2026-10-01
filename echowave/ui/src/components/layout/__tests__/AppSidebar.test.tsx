import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import Link from "next/link";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider, useSidebar } from "@/components/ui/sidebar";

import { AppSidebar } from "../AppSidebar";
const state = vi.hoisted(() => ({ pathname: "/overview", staff: false, admin: false, staffRole: "", mobile: false, shell: false }));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname, useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ provider: "local", logout: vi.fn() }) }));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ config: state.shell ? { features: { shell: true } } : null }) }));
vi.mock("@/context/TelephonyConfigWarningsContext", () => ({ useTelephonyConfigWarnings: () => ({}) }));
vi.mock("@/hooks/useAccessRoles", () => ({ useAccessRoles: () => ({ isStaff: state.staff, isOrganizationAdmin: state.admin, staffRole: state.staffRole }) }));
vi.mock("@/hooks/useLatestReleaseVersion", () => ({ useLatestReleaseVersion: () => ({}) }));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => state.mobile }));
vi.mock("@/components/layout/SidebarTeamSwitcher", () => ({ SidebarTeamSwitcher: () => null }));
vi.mock("@/components/layout/SidebarChannels", () => ({ SidebarChannels: () => <Link href="/workflow/folder/1">Team channel</Link> }));
vi.mock("@/components/layout/SidebarBots", () => ({ SidebarBots: () => <Link href="/workflow/1/thread">Agent DM</Link> }));
afterEach(cleanup);
beforeEach(() => {
  localStorage.clear();
  Object.assign(state, { pathname: "/overview", staff: false, admin: false, staffRole: "", mobile: false, shell: false });
  Element.prototype.scrollIntoView = vi.fn();
  window.matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
});
function mount(collapsed = false) { return render(<SidebarProvider defaultOpen={!collapsed}><AppSidebar /></SidebarProvider>); }
function openAccount() { fireEvent.pointerDown(screen.getByRole("button", { name: "Account menu" }), { button: 0, ctrlKey: false, pointerType: "mouse" }); }
function MobileOpener() { const { setOpenMobile } = useSidebar(); return <button onClick={() => setOpenMobile(true)}>Open navigation</button>; }

describe("stable workspace sidebar", () => {
  it.each(["/workflow/1/thread", "/channels/1"])("does not also highlight a primary destination while reading %s", (path) => {
    state.pathname = path;
    mount();
    expect(screen.getByRole("navigation", { name: "Workspace" }).querySelector('[aria-current="page"]')).toBeNull();
  });
  it.each(["Calls", "Team channel", "Agent DM"])("closes mobile navigation after following %s", (name) => {
    state.mobile = true;
    render(<SidebarProvider><MobileOpener /><AppSidebar /></SidebarProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("button", { name: "Close navigation" })).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name }));
    expect(screen.queryByRole("button", { name: "Close navigation" })).toBeNull();
  });
  it.each(["/overview", "/files", "/workflow/39", "/billing", "/usage", "/campaigns"])("keeps destinations and conversations on %s", (path) => {
    state.pathname = path;
    mount();
    const nav = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual(["Decibyl", "Tasks", "Agents", "Knowledge", "Calls"]);
    expect(screen.getByRole("link", { name: "Team channel" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Agent DM" })).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(document.querySelectorAll('a[aria-current="page"]').length).toBeLessThanOrEqual(1);
  });
  it("makes Calls navigate instead of changing the selection on Knowledge", () => {
    state.pathname = "/files";
    mount();
    const calls = screen.getByRole("link", { name: "Calls" });
    expect(calls.getAttribute("href")).toBe("/usage");
    expect(calls.hasAttribute("aria-current")).toBe(false);
    expect(screen.getByRole("link", { name: "Knowledge" }).getAttribute("aria-current")).toBe("page");
  });
  it("highlights only Calls for call detail routes", () => {
    state.pathname = "/review";
    mount();
    expect(screen.getByRole("link", { name: "Calls" }).getAttribute("aria-current")).toBe("page");
    expect(document.querySelectorAll('a[aria-current="page"]')).toHaveLength(1);
  });
  it("keeps all five direct destinations when collapsed", () => {
    mount(true);
    expect(within(screen.getByRole("navigation", { name: "Workspace" })).getAllByRole("link")).toHaveLength(5);
    expect(screen.getByRole("button", { name: "Account menu" })).toBeTruthy();
  });
  it("retains staff tier gates in direct account links", () => {
    state.staff = true;
    state.staffRole = "support";
    mount();
    openAccount();
    expect(screen.getByRole("menuitem", { name: "Review queue" }).getAttribute("href")).toBe("/superadmin/verification");
    expect(screen.queryByRole("menuitem", { name: "Staff console" })).toBeNull();
  });
});

describe("every destination on the sidebar (the shell, on for everyone since UI-0)", () => {
  it("shows every destination on the sidebar, none hidden in the profile menu", () => {
    mount();
    const manage = screen.getByRole("navigation", { name: "Manage" });
    for (const name of ["Marketplace", "Apps & tools", "Billing"]) {
      expect(within(manage).getByRole("link", { name }).hasAttribute("href")).toBe(true);
    }
    // Groups open to their pages.
    fireEvent.click(within(manage).getByRole("button", { name: "Deploy" }));
    for (const name of ["Phone numbers", "Campaigns", "Web widget", "API keys", "Webhooks & triggers"]) {
      expect(within(manage).getByRole("link", { name })).toBeTruthy();
    }
    openAccount();
    expect(screen.queryByRole("menuitem", { name: "Marketplace" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Billing" })).toBeNull();
  });

  it("opens the group of the page you are on, with that page marked", () => {
    state.pathname = "/do-not-call";
    mount();
    const manage = screen.getByRole("navigation", { name: "Manage" });
    expect(within(manage).getByRole("button", { name: "Settings" }).getAttribute("aria-expanded")).toBe("true");
    expect(within(manage).getByRole("link", { name: "Compliance" }).getAttribute("aria-current")).toBe("page");
    expect(within(manage).getByRole("button", { name: "Deploy" }).getAttribute("aria-expanded")).toBe("false");
  });

  it("names the calls row for what it opens, and drops the one-row heading", () => {
    mount();
    const nav = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual(["Decibyl", "Tasks", "Agents", "Knowledge", "Calls"]);
    expect(screen.queryByText("Activity tools")).toBeNull();
  });

  it("keeps the staff screens in the profile menu for staff", () => {
    state.staff = true;
    state.staffRole = "support";
    mount();
    openAccount();
    expect(screen.getByRole("menuitem", { name: "Review queue" }).getAttribute("href")).toBe("/superadmin/verification");
  });
});
