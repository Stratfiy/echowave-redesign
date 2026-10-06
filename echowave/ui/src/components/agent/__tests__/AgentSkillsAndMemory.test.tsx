import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  on: vi.fn(),
  shelf: vi.fn(),
  install: vi.fn((request: unknown) => Promise.resolve({ data: {}, request })),
  bots: vi.fn((request: unknown) => Promise.resolve({ data: {}, request })),
  off: vi.fn((request: unknown) => Promise.resolve({ data: {}, request })),
  own: vi.fn((request: unknown) => Promise.resolve({ data: {}, request })),
  facts: vi.fn((request: unknown) => Promise.resolve({ data: {}, request })),
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/components/memory/MemoryList", () => ({ MemoryList: () => <ul data-testid="memory-list" /> }));
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div role="menu">{children}</div>,
  DropdownMenuItem: ({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) => (
    <button type="button" role="menuitem" onClick={onClick}>{children}</button>
  ),
  DropdownMenuLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
}));
vi.mock("@/client/sdk.gen", () => ({
  skillsOnWorkflowApiV1SkillsOnWorkflowIdGet: api.on,
  listSkillsApiV1SkillsGet: api.shelf,
  installSkillApiV1SkillsInstallPost: api.install,
  setSkillBotsApiV1SkillsBotsPost: api.bots,
  takeSkillOffApiV1SkillsOffPost: api.off,
  writeOwnSkillApiV1SkillsOwnPost: api.own,
  writeFactsApiV1OrganisationMemoryFactsPost: api.facts,
}));

import { AgentMemory, memoryKey } from "../AgentMemory";
import { AgentSkills } from "../AgentSkills";

const card = (slug: string, title: string, on_bots: { id: number; name: string }[] = []) => ({
  slug, title, description: `${title} well`, division: "", emoji: "", source: "", license: "", lines: 1, on_bots,
});

function shelfWith() {
  api.on.mockResolvedValue({ data: { slugs: ["take-messages"], skills: [card("take-messages", "Take messages")] } });
  api.shelf.mockResolvedValue({
    data: {
      installed: [card("take-messages", "Take messages", [{ id: 7, name: "Riya" }]), card("book", "Book appointments", [{ id: 3, name: "Desk" }])],
      skills: [card("chase", "Chase payments")],
    },
  });
}

afterEach(() => {
  cleanup();
  Object.values(api).forEach((fn) => fn.mockClear());
});

describe("an agent's skills", () => {
  it("shows what it has, and offers the rest from the list", async () => {
    shelfWith();
    render(<AgentSkills workflowId={7} agentName="Riya" />);
    expect(await screen.findByText("Take messages")).toBeTruthy();
    const offered = screen.getAllByRole("menuitem").map((item) => item.textContent);
    expect(offered.some((t) => t?.startsWith("Book appointments"))).toBe(true);
    expect(offered.some((t) => t?.startsWith("Chase payments"))).toBe(true);
    expect(offered.some((t) => t?.startsWith("Take messages"))).toBe(false);
    expect(offered).toContain("Describe your own…");
  });

  it("adds an installed skill without taking it off the agents that have it", async () => {
    shelfWith();
    render(<AgentSkills workflowId={7} agentName="Riya" />);
    fireEvent.click(await screen.findByRole("menuitem", { name: /Book appointments/ }));
    await waitFor(() => expect(api.bots).toHaveBeenCalled());
    expect(api.install).not.toHaveBeenCalled();
    expect(api.bots.mock.calls[0][0]).toEqual({ body: { slug: "book", workflow_ids: [3, 7] } });
  });

  it("installs a skill from the catalogue first", async () => {
    shelfWith();
    render(<AgentSkills workflowId={7} agentName="Riya" />);
    fireEvent.click(await screen.findByRole("menuitem", { name: /Chase payments/ }));
    await waitFor(() => expect(api.bots).toHaveBeenCalled());
    expect(api.install.mock.calls[0][0]).toEqual({ body: { slug: "chase" } });
  });

  it("takes one off this agent", async () => {
    shelfWith();
    render(<AgentSkills workflowId={7} agentName="Riya" />);
    fireEvent.click(await screen.findByRole("button", { name: "Take Take messages off Riya" }));
    await waitFor(() => expect(api.off).toHaveBeenCalled());
    expect(api.off.mock.calls[0][0]).toEqual({ body: { slug: "take-messages", workflow_id: 7 } });
  });

  it("learns one described in plain words", async () => {
    shelfWith();
    render(<AgentSkills workflowId={7} agentName="Riya" />);
    fireEvent.click(await screen.findByRole("menuitem", { name: "Describe your own…" }));
    fireEvent.change(await screen.findByPlaceholderText("Check stock"), { target: { value: "Check stock" } });
    fireEvent.change(screen.getByPlaceholderText(/Before quoting/), { target: { value: "Look it up first." } });
    fireEvent.click(screen.getByRole("button", { name: "Teach it" }));
    await waitFor(() => expect(api.own).toHaveBeenCalled());
    expect(api.own.mock.calls[0][0]).toEqual({ body: { workflow_id: 7, title: "Check stock", description: "Look it up first." } });
  });
});

describe("an agent's memory", () => {
  it("keys a sentence by its first words", () => {
    expect(memoryKey("Clinic is closed on Sundays!")).toBe("clinic_is_closed_on_sundays");
    expect(memoryKey("   ")).toBe("note");
  });

  it("teaches this agent alone", async () => {
    render(<AgentMemory workflowId={7} agentName="Riya" />);
    fireEvent.change(screen.getByPlaceholderText("Teach Riya something to remember"), {
      target: { value: "Clinic is closed on Sundays" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.facts).toHaveBeenCalled());
    expect(api.facts.mock.calls[0][0]).toEqual({
      body: { facts: { clinic_is_closed_on_sundays: "Clinic is closed on Sundays" }, workflow_id: 7 },
    });
  });
});
