import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TeamMember } from "@/client/types.gen";
import { SidebarProvider } from "@/components/ui/sidebar";

import { AppRailV2 } from "../AppRailV2";
import type { RailData } from "../useRailData";

const state = vi.hoisted(() => ({
  pathname: "/overview",
  data: { colleagues: [], trial: null, creditsPaise: null } as RailData,
  features: {} as Record<string, boolean>,
  recents: [] as unknown[],
}));

vi.mock("next/navigation", () => ({ usePathname: () => state.pathname, useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { displayName: "Priya" }, loading: false, logout: vi.fn() }) }));
vi.mock("@/hooks/useAccessRoles", () => ({ useAccessRoles: () => ({ isStaff: false, isOrganizationAdmin: true, staffRole: "" }) }));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => false }));
vi.mock("@/components/layout/OrganizationSwitcher", () => ({ OrganizationSwitcher: () => <span>Sri Lakshmi Dental</span> }));
vi.mock("../useRailData", () => ({ useRailData: () => state.data }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(state.features[name]) }));
vi.mock("@/client/sdk.gen", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  recentsApiV1TimelineRecentsGet: vi.fn(async () => ({ data: { items: state.recents } })),
}));

function member(overrides: Partial<TeamMember>): TeamMember {
  return {
    workflow_id: 1,
    workflow_uuid: null,
    name: "reception",
    is_live: false,
    status: "",
    tone: "idle",
    at: null,
    calls: 0,
    answered: 0,
    outcomes: 0,
    failures: 0,
    last_action: null,
    ...overrides,
  };
}

function mount() {
  return render(
    <SidebarProvider defaultOpen>
      <AppRailV2 />
    </SidebarProvider>,
  );
}

afterEach(cleanup);
beforeEach(() => {
  state.features = {};
  state.pathname = "/overview";
  state.data = { colleagues: [], trial: null, creditsPaise: null };
  state.recents = [];
});

describe("v2 rail", () => {
  it("renders the homes in order", () => {
    mount();
    const nav = screen.getByRole("navigation", { name: "Homes" });
    const labels = within(nav)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(labels).toEqual(["Home", "Tasks", "Agents", "Activity", "Settings"]);
  });

  it("maps each home onto an existing route", () => {
    mount();
    const nav = screen.getByRole("navigation", { name: "Homes" });
    const hrefs = within(nav)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(hrefs).toEqual(["/overview", "/tasks", "/workflow", "/usage", "/settings"]);
  });

  it("lights Activity on a campaigns page", () => {
    state.pathname = "/campaigns/4";
    mount();
    expect(screen.getByRole("link", { name: "Activity" }).getAttribute("aria-current")).toBe("page");
    expect(document.querySelectorAll('nav[aria-label="Homes"] a[aria-current="page"]')).toHaveLength(1);
  });

  it("lists recent conversations, newest first, not every agent again", async () => {
    state.data = { ...state.data, colleagues: [member({ workflow_id: 9, name: "followup" })] };
    state.recents = [
      {
        kind: "agent",
        key: "a-3",
        title: "Riya",
        subtitle: "Booked Mr Rao for 4pm",
        href: "/workflow/3/thread",
        workflow_id: 3,
        avatar: null,
      },
      {
        kind: "decibyl",
        key: "t-abc",
        title: "Plan my week",
        subtitle: null,
        href: "/overview?thread=abc",
        workflow_id: null,
        avatar: null,
      },
    ];
    mount();
    await waitFor(() => expect(screen.getByText("Recents")).toBeTruthy());
    const list = screen.getByTestId("v2-recents");
    const links = within(list).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/workflow/3/thread", "/overview?thread=abc"]);
    expect(within(list).getByText("Booked Mr Rao for 4pm")).toBeTruthy();
    // An agent nobody has talked to is on the Agents page, not here.
    expect(within(list).queryByText("followup")).toBeNull();
    expect(screen.queryByText("Colleagues")).toBeNull();
  });

  it("shows no Recents heading before there is anything recent", () => {
    mount();
    expect(screen.queryByText("Recents")).toBeNull();
  });

  it("counts agents and what needs you on the homes", () => {
    state.data = {
      ...state.data,
      colleagues: [member({ workflow_id: 7, tone: "attention" }), member({ workflow_id: 3 })],
    };
    mount();
    const countOf = (name: RegExp) => screen.getByRole("link", { name }).querySelector(".v2-count")?.textContent;
    expect(countOf(/^Agents/)).toBe("2");
    expect(countOf(/^Home/)).toBe("1");
    expect(countOf(/^Tasks/)).toBeUndefined();
  });

  it("hides the trial box when there is no trial and no credits", () => {
    mount();
    expect(screen.queryByTestId("v2-trial-box")).toBeNull();
  });

  it("shows credits only when there is no trial data", () => {
    state.data = { ...state.data, creditsPaise: 71_200 };
    mount();
    expect(screen.getByTestId("v2-trial-box")).toBeTruthy();
    expect(screen.queryByTestId("v2-trial-days")).toBeNull();
    expect(screen.getByText("Credits")).toBeTruthy();
  });

  it("shows no trial or credits while everything is free", () => {
    state.features = { free_mode: true };
    state.data = { ...state.data, trial: { onTrial: true, active: true, daysLeft: 11, days: 14 }, creditsPaise: 71_200 };
    mount();
    expect(screen.queryByTestId("v2-trial-box")).toBeNull();
  });

  it("shows days left when the plan reports a trial", () => {
    state.data = { ...state.data, trial: { onTrial: true, active: true, daysLeft: 11, days: 14 }, creditsPaise: 71_200 };
    mount();
    expect(screen.getByText("11 days left")).toBeTruthy();
    expect(screen.getByRole("meter").getAttribute("aria-valuenow")).toBe("79");
  });

  it("never says bot to the reader", () => {
    state.data = { ...state.data, colleagues: [member({ workflow_id: 3, is_live: true, tone: "working" })] };
    mount();
    expect(document.body.textContent ?? "").not.toMatch(/\bbots?\b/i);
  });

  it("shows Studio only for a workspace with the studio flag", () => {
    mount();
    expect(screen.queryByRole("link", { name: "Studio" })).toBeNull();
    cleanup();
    state.features = { studio: true };
    mount();
    const nav = screen.getByRole("navigation", { name: "Homes" });
    const labels = within(nav).getAllByRole("link").map((link) => link.textContent);
    expect(labels.indexOf("Studio")).toBe(labels.indexOf("Agents") + 1);
  });

  it("keeps the channels off the rail; Agents lights for them", () => {
    state.pathname = "/channels/3";
    mount();
    expect(screen.queryByText("Channels")).toBeNull();
    expect(screen.getByRole("link", { name: "Agents" }).getAttribute("aria-current")).toBe("page");
  });

  it("lights Settings on a page it holds", () => {
    state.pathname = "/billing";
    mount();
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("aria-current")).toBe("page");
    expect(document.querySelectorAll('nav[aria-label="Homes"] a[aria-current="page"]')).toHaveLength(1);
  });
});
