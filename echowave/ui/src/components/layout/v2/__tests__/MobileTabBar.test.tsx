import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sidebar = { toggleSidebar: vi.fn(), openMobile: false };
let pathname = "/tasks";

vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("@/components/ui/sidebar", () => ({ useSidebar: () => sidebar }));
vi.mock("@/lib/features", () => ({ useFeature: () => false }));

import { MobileTabBar } from "../MobileTabBar";

describe("MobileTabBar", () => {
  beforeEach(() => {
    sidebar.toggleSidebar.mockClear();
    sidebar.openMobile = false;
    pathname = "/tasks";
  });

  it("puts Chat and Today a tap away and lights the one you are on", () => {
    render(<MobileTabBar />);
    expect(screen.getByRole("link", { name: "Chat" }).getAttribute("href")).toBe("/overview");
    const today = screen.getByRole("link", { name: "Today" });
    expect(today.getAttribute("href")).toBe("/tasks");
    expect(today.getAttribute("aria-current")).toBe("page");
    expect(screen.queryByRole("link", { name: "Studio" })).toBeNull();
  });

  it("keeps Today lit on its Activity pages", () => {
    pathname = "/usage";
    render(<MobileTabBar />);
    expect(screen.getByRole("link", { name: "Today" }).getAttribute("aria-current")).toBe("page");
  });

  it("lights nothing on the agent list or in Settings", () => {
    for (const path of ["/workflow", "/settings/models"]) {
      pathname = path;
      const { unmount } = render(<MobileTabBar />);
      expect(screen.getByRole("link", { name: "Chat" }).getAttribute("aria-current")).toBeNull();
      expect(screen.getByRole("link", { name: "Today" }).getAttribute("aria-current")).toBeNull();
      unmount();
    }
  });

  it("opens the drawer from Menu", () => {
    render(<MobileTabBar />);
    fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    expect(sidebar.toggleSidebar).toHaveBeenCalledOnce();
  });
});
