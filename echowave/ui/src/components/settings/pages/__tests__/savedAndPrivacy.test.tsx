/**
 * Saved items (screen 15) and Privacy and security (screen 25).
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  item: vi.fn(),
  search: vi.fn(),
  del: vi.fn(),
  privacy: vi.fn(),
  preview: vi.fn(),
  deletion: vi.fn(),
  params: new URLSearchParams(),
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => api.params }));
vi.mock("@/components/MfaSection", () => ({ MfaSection: () => <p>MFA controls</p> }));
vi.mock("@/client/sdk.gen", () => ({
  mySavedApiV1MeSavedGet: api.list,
  savedItemApiV1MeSavedItemIdGet: api.item,
  searchMineApiV1MeSearchGet: api.search,
  deleteSavedItemApiV1MeSavedItemIdDeletePost: api.del,
  renameSavedItemApiV1MeSavedItemIdPatch: vi.fn(),
  settleSettingsCardApiV1MeSettingsCardsEventIdSettlePost: vi.fn(),
  settingsCardApiV1MeSettingsCardsEventIdGet: vi.fn(),
  myPrivacyApiV1MePrivacyGet: api.privacy,
  privacyPreviewApiV1MePrivacyPreviewGet: api.preview,
  requestMyDeletionApiV1MePrivacyDeletionPost: api.deletion,
  requestMyExportApiV1MePrivacyExportPost: vi.fn(),
  myDataRequestApiV1MePrivacyRequestsRequestIdGet: vi.fn(),
  downloadMyExportApiV1MePrivacyRequestsRequestIdDownloadGet: vi.fn(),
}));

import { PrivacySettings } from "../PrivacySettings";
import { SavedSettings } from "../SavedSettings";

const item = (id: number, title: string, extra = {}) => ({
  id,
  organization_id: 2,
  kind: "reply",
  title,
  body: "Your GST is due on the 20th.",
  visibility: "private",
  status: "ready",
  mine: true,
  conversation_href: "/overview",
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
  deletion_effects: ["Only this saved copy is deleted.", "The conversation it came from stays as it is."],
  ...extra,
});

beforeEach(() => {
  api.params = new URLSearchParams();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Saved items", () => {
  it("lists name, type and date rows for the scope it says, and a new scope clears the old rows first", async () => {
    let releaseWorkspace: (v: unknown) => void = () => {};
    api.list
      .mockResolvedValueOnce({ data: { scope: "personal", items: [item(1, "GST due date")] } })
      .mockReturnValueOnce(new Promise((resolve) => (releaseWorkspace = resolve)));
    render(<SavedSettings />);
    expect(await screen.findByText("GST due date")).toBeTruthy();
    expect(screen.getByText("Your saved items")).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Workspace" }));
    await waitFor(() => expect(screen.queryByText("GST due date")).toBeNull());
    releaseWorkspace({ data: { scope: "workspace", items: [] } });
    expect(await screen.findByText(/Nothing shared with the workspace yet/)).toBeTruthy();
  });

  it("a failed list is an error with retry, not an empty list", async () => {
    api.list.mockResolvedValueOnce({ error: { detail: "boom" } });
    render(<SavedSettings />);
    expect(await screen.findByText("Could not load saved items")).toBeTruthy();
    expect(screen.queryByText(/Nothing saved yet/)).toBeNull();
  });

  it("delete says what it does not touch, then asks on a card", async () => {
    api.params = new URLSearchParams("item=1");
    api.list.mockResolvedValue({ data: { scope: "personal", items: [item(1, "GST due date")] } });
    api.item.mockResolvedValue({ data: item(1, "GST due date") });
    api.del.mockResolvedValue({
      data: { event_id: 5, organization_id: 2, label: "Delete a saved item", effect: "Only this saved copy is deleted.", state: "proposed", version: "v1", reversible: true, args: {} },
    });
    render(<SavedSettings />);
    const preview = await screen.findByTestId("saved-preview");
    expect(within(preview).getByRole("link", { name: "Back to the conversation" }).getAttribute("href")).toBe("/overview");
    fireEvent.click(within(preview).getByRole("button", { name: "Delete" }));
    expect(await within(preview).findByTestId("settings-card")).toBeTruthy();
    expect(within(preview).getByText("The conversation it came from stays as it is.")).toBeTruthy();
  });
});

const privacy = (extra = {}) => ({
  data: {
    retention: {
      recording_days: 30,
      transcript_days: 90,
      is_platform_default: true,
      temporary_conversation_hours: 24,
      export_days: 7,
      managed_by_workspace: true,
      workspace_name: "Acme Clinic",
    },
    security: { mfa_enabled: false, has_password: true },
    deletion_available: true,
    workspace_owner: false,
    delete_phrase: "delete my data",
    requests: [],
    ...extra,
  },
});

describe("Privacy and security", () => {
  it("shows the effective retention and who manages it, and MFA first", async () => {
    api.privacy.mockResolvedValue(privacy());
    render(<PrivacySettings />);
    expect(await screen.findByText("MFA controls")).toBeTruthy();
    expect(screen.getByText("30 days")).toBeTruthy();
    expect(screen.getByText(/Managed by your workspace/)).toBeTruthy();
  });

  it("without a personal space, deletion says it is unavailable rather than pretending", async () => {
    api.privacy.mockResolvedValue(privacy({ deletion_available: false }));
    render(<PrivacySettings />);
    expect(await screen.findByTestId("deletion-unavailable")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /See what would be deleted/ })).toBeNull();
  });

  it("deletion shows every store and exception, needs the phrase, then raises a card", async () => {
    api.privacy.mockResolvedValue(privacy());
    api.preview.mockResolvedValue({
      data: {
        kind: "deletion",
        scope: "personal",
        stores: [{ store: "memory", label: "Your personal memory", count: 4 }],
        exceptions: [{ store: "sign_in", label: "Your sign-in account", exception: "Kept until a person at Decibyl closes it." }],
        lines: ["This cannot be undone."],
      },
    });
    api.deletion.mockResolvedValue({
      data: {
        id: 9,
        kind: "deletion",
        status: "awaiting_approval",
        stores: [],
        created_at: "2026-10-08T10:00:00Z",
        card: { event_id: 4, organization_id: 11, label: "Delete your personal data", effect: "This cannot be undone.", state: "proposed", version: "v9", reversible: false, args: {} },
      },
    });
    render(<PrivacySettings />);
    fireEvent.click(await screen.findByRole("button", { name: "See what would be deleted" }));
    const list = await screen.findByTestId("privacy-preview-deletion");
    expect(within(list).getByText("Your personal memory")).toBeTruthy();
    expect(within(list).getByText(/Your sign-in account/)).toBeTruthy();
    const ask = screen.getByRole("button", { name: "Ask to delete my data" });
    expect(ask.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText(/Type/), { target: { value: "Delete my data" } });
    expect(ask.hasAttribute("disabled")).toBe(false);
    fireEvent.click(ask);
    await waitFor(() => expect(api.deletion).toHaveBeenCalledWith({ body: { phrase: "Delete my data" } }));
    expect(await screen.findByTestId("settings-card")).toBeTruthy();
    expect(screen.getByText("This cannot be undone.", { selector: "dd" })).toBeTruthy();
  });
});
