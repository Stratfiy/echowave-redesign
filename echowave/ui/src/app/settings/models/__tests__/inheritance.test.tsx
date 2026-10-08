/**
 * Model defaults showing inheritance (screen 26): with the switch on, each
 * slot says where it comes from and whether it can run; saves carry the
 * slot's revision; a conflict says so and shows what runs now.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), oldGet: vi.fn(), toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => name === "model_inheritance" }));
vi.mock("sonner", () => ({ toast: api.toast }));
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div role="menu">{children}</div>,
  DropdownMenuItem: ({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) => (
    <button type="button" role="menuitem" onClick={onClick}>
      {children}
    </button>
  ),
  DropdownMenuLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
}));
vi.mock("@/client/sdk.gen", () => ({
  getWorkspaceModelsApiV1OrganizationsModelsGet: api.oldGet,
  setWorkspaceModelApiV1OrganizationsModelsPut: vi.fn(),
  modelInheritanceApiV1SettingsModelsInheritanceGet: api.get,
  chooseModelDefaultApiV1SettingsModelsInheritancePut: api.put,
  setProviderKeyApiV1ProviderKeysPut: vi.fn(),
}));

import ModelsPage from "../page";

const view = (current: string, label: string, extra: Record<string, unknown> = {}) => ({
  data: {
    locked: null,
    credential_component: { llm: "llm" },
    precedence: ["Decibyl's allowed models and policy", "This workspace's choice", "An agent's own override, where one is allowed"],
    agents: [
      { workflow_id: 4, name: "Front desk", slots: { llm: { inherits: true, label: "Inherits workspace" }, tts: { inherits: false, label: "Sarvam · bulbul:v3" } } },
    ],
    slots: [
      {
        key: "llm",
        label: "Brain",
        blurb: "Thinks",
        current,
        current_label: label,
        source: "platform",
        readiness: "needs_setup",
        readiness_reason: "Decibyl's Anthropic key is not set up on this deployment yet, so this cannot run until it is.",
        revision: `rev-${current}`,
        fallback: [],
        ours: [
          { value: "tier:auto", label: "Auto", blurb: "", serves: "Claude" },
          { value: "tier:accurate", label: "Smart", blurb: "", serves: "Claude Sonnet" },
        ],
        more: [],
        own: [],
        ...extra,
      },
    ],
  },
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Settings -> Models with inheritance", () => {
  it("says where each value comes from and that it needs setup, and lists agents", async () => {
    api.get.mockResolvedValue(view("tier:auto", "Auto"));
    render(<ModelsPage />);
    const brain = await screen.findByTestId("inheritance-llm");
    expect(brain.getAttribute("data-readiness")).toBe("needs_setup");
    expect(screen.getByText("Decibyl's default")).toBeTruthy();
    expect(screen.getByText(/not set up on this deployment/)).toBeTruthy();
    expect(screen.getByText("Front desk")).toBeTruthy();
    expect(screen.getByText("Inherits workspace")).toBeTruthy();
    expect(api.oldGet).not.toHaveBeenCalled();
  });

  it("saves with the slot's revision, and a conflict shows what runs now", async () => {
    api.get.mockResolvedValueOnce(view("tier:auto", "Auto")).mockResolvedValueOnce(view("tier:accurate", "Smart", { source: "workspace" }));
    api.put.mockResolvedValue({ error: { detail: { message: "changed" } }, response: { status: 409 } });
    render(<ModelsPage />);
    await screen.findByTestId("inheritance-llm");
    fireEvent.click(screen.getByRole("menuitem", { name: /Smart/ }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith({ body: { slot: "llm", value: "tier:accurate", revision: "rev-tier:auto" } }));
    await waitFor(() => expect(api.toast.error).toHaveBeenCalledWith("This was changed somewhere else. Showing what runs now."));
    await waitFor(() => expect(screen.getByTestId("inheritance-llm").textContent).toContain("This workspace's choice"));
  });
});
