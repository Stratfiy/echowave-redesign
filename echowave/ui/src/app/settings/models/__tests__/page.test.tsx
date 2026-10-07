import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div role="menu">{children}</div>,
  DropdownMenuItem: ({
    children,
    onClick,
    disabled,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
  }) => (
    <button type="button" role="menuitem" onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
  DropdownMenuLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
}));
vi.mock("@/client/sdk.gen", () => ({
  getWorkspaceModelsApiV1OrganizationsModelsGet: api.get,
  setWorkspaceModelApiV1OrganizationsModelsPut: api.put,
  setProviderKeyApiV1ProviderKeysPut: vi.fn(),
}));

import ModelsPage from "../page";

const view = (current: string, label: string) => ({
  data: {
    locked: null,
    credential_component: { llm: "llm" },
    slots: [
      {
        key: "llm",
        label: "Brain",
        blurb: "Thinks",
        current,
        current_label: label,
        ours: [
          { value: "tier:default", label: "Everyday", blurb: "", serves: "Claude Haiku" },
          { value: "tier:accurate", label: "Smart", blurb: "", serves: "Claude Sonnet" },
        ],
        more: [],
        own: [],
      },
    ],
  },
});

afterEach(() => {
  cleanup();
  api.get.mockReset();
  api.put.mockReset();
});

describe("Settings -> Models", () => {
  it("saves one choice at a time per slot", async () => {
    api.get.mockResolvedValue(view("tier:default", "Everyday"));
    let release: (v: unknown) => void = () => {};
    api.put.mockReturnValue(new Promise((resolve) => (release = resolve)));
    render(<ModelsPage />);
    await screen.findByText("Brain");
    fireEvent.click(screen.getByRole("menuitem", { name: /Smart/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Brain: .* Change/ }).hasAttribute("disabled")).toBe(true));
    fireEvent.click(screen.getByRole("menuitem", { name: /Everyday/ }));
    expect(api.put).toHaveBeenCalledTimes(1);
    release(view("tier:accurate", "Smart"));
    await waitFor(() => expect(screen.getByRole("button", { name: /Brain: Smart/ })).toBeTruthy());
  });

  it("lists a choice that is still being set up, and does not let it be picked", async () => {
    const withAws = view("tier:default", "Everyday");
    withAws.data.slots[0].ours.push({
      value: "tier:aws",
      label: "Multilingual (AWS)",
      blurb: "",
      serves: "Amazon Bedrock",
      status: "needs_setup",
      status_note: "Being set up by Decibyl. Not available to choose yet.",
    } as (typeof withAws.data.slots)[0]["ours"][0]);
    api.get.mockResolvedValue(withAws);
    render(<ModelsPage />);
    await screen.findByText("Brain");
    const item = screen.getByRole("menuitem", { name: /Multilingual \(AWS\)/ });
    expect(item.textContent).toContain("Needs setup");
    expect(item.textContent).toContain("Being set up by Decibyl");
    expect(item.hasAttribute("disabled")).toBe(true);
    fireEvent.click(item);
    expect(api.put).not.toHaveBeenCalled();
  });

  it("keeps the saved value when the network fails", async () => {
    api.get.mockResolvedValue(view("tier:default", "Everyday"));
    api.put.mockRejectedValue(new Error("offline"));
    render(<ModelsPage />);
    await screen.findByText("Brain");
    fireEvent.click(screen.getByRole("menuitem", { name: /Smart/ }));
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: /Brain: Everyday/ })).toBeTruthy();
  });
});
