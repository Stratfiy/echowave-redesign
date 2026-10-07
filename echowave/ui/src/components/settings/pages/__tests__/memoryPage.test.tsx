/**
 * The memory manager (screen 16). Acceptance: a simulated 500 shows retry and
 * never "nothing remembered", and recovering restores the same facts; memory
 * starts off and the switch rolls back on refusal; a fact shows where it came
 * from; forgetting is a card confirmed on the version shown.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  memory: vi.fn(),
  fact: vi.fn(),
  toggle: vi.fn(),
  forget: vi.fn(),
  settle: vi.fn(),
  card: vi.fn(),
  push: vi.fn(),
  params: new URLSearchParams(),
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: api.push }), useSearchParams: () => api.params }));
vi.mock("@/client/sdk.gen", () => ({
  myMemoryApiV1MeMemoryGet: api.memory,
  memoryFactApiV1MeMemoryFactIdGet: api.fact,
  setMemorySwitchApiV1MeMemorySwitchPut: api.toggle,
  forgetMemoryFactApiV1MeMemoryFactIdForgetPost: api.forget,
  settleSettingsCardApiV1MeSettingsCardsEventIdSettlePost: api.settle,
  settingsCardApiV1MeSettingsCardsEventIdGet: api.card,
  confirmMemoryFactApiV1MeMemoryFactIdConfirmPost: vi.fn(),
  editMemoryFactApiV1MeMemoryFactIdPatch: vi.fn(),
  memoryDestinationsApiV1MeMemoryDestinationsGet: vi.fn().mockResolvedValue({ data: [] }),
  shareMemoryFactApiV1MeMemoryFactIdSharePost: vi.fn(),
  shareMemoryPreviewApiV1MeMemoryFactIdSharePreviewGet: vi.fn(),
  startTemporaryConversationApiV1MeTemporaryConversationsPost: vi.fn(),
}));

import { MemorySettings } from "../MemorySettings";

const source = { kind: "conversation", line: "From your conversations with Decibyl", first_seen_at: "2026-10-01T10:00:00Z", times_seen: 1 };
const tea = { id: 7, key: "tea", value: "masala, no sugar", kind: "fact", status: "confirmed", scope: "mine", source, saved_at: "2026-10-01T10:00:00Z", revisions: 0 };
const hours = { ...tea, id: 8, key: "opening_hours", value: "9 to 6", scope: "workspace", source: { ...source, line: "Told to Decibyl" } };
const overview = (extra = {}) => ({
  data: {
    memory_enabled: false,
    memory_chosen: false,
    revision: 2,
    personal_memory: true,
    mine: [tea],
    workspace: [hours],
    temporary_retention: "Nothing from it is saved to memory, and its messages are deleted 24 hours after it starts.",
    ...extra,
  },
});

beforeEach(() => {
  api.params = new URLSearchParams();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Memory manager", () => {
  it("a 500 is an error with retry, and retry restores the same facts", async () => {
    api.memory.mockResolvedValueOnce({ error: { detail: "Internal Server Error" } }).mockResolvedValueOnce(overview());
    render(<MemorySettings />);
    expect(await screen.findByText("Could not load what is remembered")).toBeTruthy();
    expect(screen.queryByText(/Nothing of yours is remembered/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));
    expect(await screen.findByText("masala, no sugar")).toBeTruthy();
    expect(screen.getByText("From your conversations with Decibyl")).toBeTruthy();
    expect(screen.getByText("Only you")).toBeTruthy();
  });

  it("starts off until chosen, and a refused switch goes back to what is saved", async () => {
    api.memory.mockResolvedValue(overview());
    api.toggle.mockResolvedValue({ error: { detail: "nope" }, response: { status: 500 } });
    render(<MemorySettings />);
    expect(await screen.findByText(/Off until you choose/)).toBeTruthy();
    const toggle = screen.getByTestId("memory-switch");
    fireEvent.click(toggle);
    await waitFor(() => expect(api.toggle).toHaveBeenCalledWith({ body: { memory_enabled: true, revision: 2 } }));
    expect(await screen.findByText(/not saved/)).toBeTruthy();
    expect(toggle.getAttribute("data-state")).toBe("unchecked");
  });

  it("the workspace's memories are apart from yours", async () => {
    api.memory.mockResolvedValue(overview());
    render(<MemorySettings />);
    await screen.findByText("masala, no sugar");
    expect(screen.queryByText("9 to 6")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /Workspace/ }));
    expect(screen.getByText("9 to 6")).toBeTruthy();
  });

  it("forget is a card, confirmed on the version shown", async () => {
    api.params = new URLSearchParams("fact=7");
    api.memory.mockResolvedValue(overview());
    api.fact.mockResolvedValue({ data: { ...tea, history: [] } });
    const card = { event_id: 91, organization_id: 3, label: "Forget one of your memories", effect: "Decibyl stops using it.", state: "proposed", version: "abc123", reversible: true, args: {} };
    api.forget.mockResolvedValue({ data: card });
    api.settle.mockResolvedValue({ data: { ...card, state: "armed" } });
    api.card.mockResolvedValue({ data: { ...card, state: "done", note: "Forgotten." } });
    render(<MemorySettings />);
    const detail = await screen.findByTestId("memory-detail");
    expect(within(detail).getByText("Where it came from")).toBeTruthy();
    fireEvent.click(within(detail).getByRole("button", { name: "Forget" }));
    const panel = await screen.findByTestId("settings-card");
    fireEvent.click(within(panel).getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(api.settle).toHaveBeenCalledWith({ path: { event_id: 91 }, body: { organization_id: 3, verb: "confirm", version: "abc123" } }),
    );
    expect(await within(panel).findByText(/Starting in a few seconds/)).toBeTruthy();
  });
});
