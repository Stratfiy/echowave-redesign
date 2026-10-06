import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  update: vi.fn((request: unknown) => Promise.resolve({ data: {}, request })),
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/components/telephony/CarriersSection", () => ({ CarriersSection: () => <section id="carriers">carriers</section> }));
vi.mock("@/components/avatar/AgentAvatar", () => ({ AgentAvatar: () => <span data-testid="face" /> }));
// The menu's items render inline, so a test can click them without a pointer.
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div role="menu">{children}</div>,
  DropdownMenuItem: ({ children, onClick, asChild, disabled }: { children: React.ReactNode; onClick?: () => void; asChild?: boolean; disabled?: boolean }) =>
    asChild ? <>{children}</> : <button type="button" role="menuitem" disabled={disabled} onClick={onClick}>{children}</button>,
  DropdownMenuLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
}));
vi.mock("@/client/sdk.gen", () => ({
  listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet: async () => ({
    data: { configurations: [{ id: 1, name: "Decibyl", provider: "plivo", is_platform_managed: true, is_default_outbound: true }] },
  }),
  listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet: async () => ({
    data: {
      phone_numbers: [
        { id: 10, address: "+918047182290", is_active: true, inbound_workflow_id: 7, inbound_workflow_name: "Riya" },
        { id: 11, address: "+918047182291", is_active: true, inbound_workflow_id: null },
      ],
    },
  }),
  listNumbersApiV1VerifiedNumbersGet: async () => ({ data: [] }),
  getWorkflowsSummaryApiV1WorkflowSummaryGet: async () => ({ data: [{ id: 7, name: "Riya" }, { id: 9, name: "Accounts" }] }),
  updatePhoneNumberApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersPhoneNumberIdPut: api.update,
}));

import PhoneNumbersPage from "../page";

afterEach(() => {
  cleanup();
  api.update.mockClear();
});

describe("Settings -> Phone numbers", () => {
  it("lists every number with who uses it", async () => {
    render(<PhoneNumbersPage />);
    const list = await screen.findByTestId("number-list");
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByRole("button", { name: /Riya answers \+918047182290/ })).toBeTruthy();
    expect(within(rows[1]).getByRole("button", { name: /Nobody answers \+918047182291/ })).toBeTruthy();
    expect(within(rows[0]).getByText(/Bought/)).toBeTruthy();
  });

  it("changes who answers from the chip", async () => {
    render(<PhoneNumbersPage />);
    const list = await screen.findByTestId("number-list");
    const second = within(list).getAllByRole("listitem")[1];
    fireEvent.click(within(second).getByRole("menuitem", { name: /Accounts/ }));
    await waitFor(() => expect(api.update).toHaveBeenCalled());
    expect(api.update).toHaveBeenCalledWith({
      path: { config_id: 1, phone_number_id: 11 },
      body: { inbound_workflow_id: 9 },
    });
  });

  it("lets a number go back to nobody", async () => {
    render(<PhoneNumbersPage />);
    const list = await screen.findByTestId("number-list");
    const first = within(list).getAllByRole("listitem")[0];
    fireEvent.click(within(first).getByRole("menuitem", { name: "Nobody" }));
    await waitFor(() => expect(api.update).toHaveBeenCalled());
    expect(api.update.mock.calls[0][0]).toEqual({
      path: { config_id: 1, phone_number_id: 10 },
      body: { clear_inbound_workflow: true },
    });
  });

  it("keeps the carriers below the list", async () => {
    render(<PhoneNumbersPage />);
    expect(await screen.findByText("carriers")).toBeTruthy();
  });
});
