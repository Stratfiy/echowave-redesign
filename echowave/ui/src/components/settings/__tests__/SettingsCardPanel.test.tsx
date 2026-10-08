/**
 * A confirmed Settings card follows the server until it settles.
 *
 * Found by the end-to-end browser suite: after Approve the card said
 * "Starting in a few seconds" forever. It polled once, got "armed" back
 * (the undo window is ten seconds), and never asked again, because the
 * poll only re-armed when the state *changed*.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ get: vi.fn(), settle: vi.fn() }));

vi.mock("@/client/sdk.gen", () => ({
  settingsCardApiV1MeSettingsCardsEventIdGet: api.get,
  settleSettingsCardApiV1MeSettingsCardsEventIdSettlePost: api.settle,
}));

import { SettingsCardPanel } from "../SettingsCardPanel";

const card = (state: string, extra = {}) => ({
  event_id: 7,
  organization_id: 2,
  action: "delete_saved_item",
  label: "Delete a saved item",
  effect: "Deletes the saved copy of 'GST note'.",
  state,
  ledger_state: null,
  version: "v1",
  reversible: true,
  fires_at: null,
  note: null,
  error: null,
  args: { item_id: 1, title: "GST note" },
  ...extra,
});

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("SettingsCardPanel", () => {
  it("keeps following an armed card until it is done", async () => {
    api.get
      .mockResolvedValueOnce({ data: card("armed") })
      .mockResolvedValueOnce({ data: card("armed") })
      .mockResolvedValueOnce({ data: card("running") })
      .mockResolvedValue({ data: card("done", { note: "Deleted." }) });
    const onSettled = vi.fn();

    render(<SettingsCardPanel card={card("armed") as never} onSettled={onSettled} pollMs={1000} />);
    for (let i = 0; i < 6; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    }

    expect(screen.getByTestId("settings-card").getAttribute("data-state")).toBe("done");
    expect(screen.getByText("Deleted.")).toBeTruthy();
    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it("stops asking once the card has settled", async () => {
    api.get.mockResolvedValue({ data: card("done", { note: "Deleted." }) });
    render(<SettingsCardPanel card={card("armed") as never} pollMs={1000} />);
    for (let i = 0; i < 5; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    }
    expect(api.get).toHaveBeenCalledTimes(1);
  });
});
