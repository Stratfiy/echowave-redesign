import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  flags: {} as Record<string, boolean>,
  pathname: "/settings/personalization",
  admin: false,
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => state.pathname,
  useRouter: () => ({ push: state.push }),
}));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(state.flags[name]) }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/hooks/useAccessRoles", () => ({ useAccessRoles: () => ({ isOrganizationAdmin: state.admin, loaded: true }) }));
vi.mock("@/client/sdk.gen", () => ({
  listMyOrganizationsApiV1OrganizationsMineGet: vi.fn().mockResolvedValue({
    data: [
      { id: 1, name: "Personal", is_selected: false, role: "owner" },
      { id: 2, name: "Acme Clinic", is_selected: true, role: "member" },
    ],
  }),
}));

import { SettingsNav } from "../SettingsNav";

beforeEach(() => {
  state.flags = { settings_shell: true, memory_manager: true, privacy_center: true };
  state.pathname = "/settings/personalization";
  state.admin = false;
  state.push.mockReset();
});
afterEach(cleanup);

describe("Settings navigation", () => {
  it("off: the list is exactly as it was", () => {
    state.flags = {};
    render(<SettingsNav />);
    expect(screen.queryByTestId("settings-shell-nav")).toBeNull();
    expect(screen.getAllByText("General").length).toBeGreaterThan(0);
  });

  it("on: Personal, Connections, Privacy, Advanced, then the workspace by name", async () => {
    render(<SettingsNav />);
    const nav = screen.getByTestId("settings-shell-nav");
    for (const group of ["Personal", "Connections", "Privacy", "Advanced"]) {
      expect(within(nav).getByText(group, { selector: "h2" })).toBeTruthy();
    }
    await waitFor(() => expect(within(nav).getByText("Acme Clinic", { selector: "h2" })).toBeTruthy());
    expect(within(nav).getAllByText("Memory").length).toBeGreaterThan(0);
    // A member does not see the admin-only sections.
    expect(within(nav).queryAllByText("Developer")).toHaveLength(0);
    const current = within(nav).getAllByRole("link", { name: "Personalization" }).find((a) => a.getAttribute("aria-current") === "page");
    expect(current).toBeTruthy();
  });

  it("searches with everyday words, and Enter opens the first match", () => {
    render(<SettingsNav />);
    const box = screen.getByTestId("settings-search");
    fireEvent.change(box, { target: { value: "mic" } });
    const results = screen.getByTestId("settings-search-results");
    expect(within(results).getByText("Microphone")).toBeTruthy();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(state.push).toHaveBeenCalledWith("/settings/voice#microphone");
  });

  it("says when nothing matches", () => {
    render(<SettingsNav />);
    fireEvent.change(screen.getByTestId("settings-search"), { target: { value: "zzzqqq" } });
    expect(screen.getByText(/Nothing in Settings matches/)).toBeTruthy();
  });

  it("a switched-off section is not in the list or in search", () => {
    state.flags = { settings_shell: true };
    render(<SettingsNav />);
    expect(screen.queryAllByText("Memory")).toHaveLength(0);
    fireEvent.change(screen.getByTestId("settings-search"), { target: { value: "temporary" } });
    expect(screen.getByText(/Nothing in Settings matches/)).toBeTruthy();
  });
});
