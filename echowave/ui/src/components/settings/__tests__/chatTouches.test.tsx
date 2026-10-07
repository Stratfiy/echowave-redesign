/** Save under a reply, and the temporary-conversation line (screens 15, 16). */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ save: vi.fn(), temp: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
  saveItemApiV1MeSavedPost: api.save,
  temporaryConversationApiV1MeTemporaryConversationsThreadIdGet: api.temp,
}));

import { SaveReplyButton } from "../SaveReplyButton";
import { TemporaryBanner } from "../TemporaryBanner";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const reply = { id: 12, kind: "message", actor: "agent", summary: "Your GST is due on the 20th.", payload: { body: "Your GST is due on the 20th.\nPay ₹12,400.", from: "Decibyl" } };

describe("Save under a reply", () => {
  it("saves with its way back and says saved only when the server did", async () => {
    api.save.mockResolvedValue({ data: { id: 31 } });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    render(<SaveReplyButton event={reply as any} threadId="abc" />);
    fireEvent.click(screen.getByRole("button", { name: "Save this reply" }));
    await waitFor(() =>
      expect(api.save).toHaveBeenCalledWith({ body: { title: "Your GST is due on the 20th.", kind: "reply", source_event_id: 12, thread_id: "abc" } }),
    );
    expect((await screen.findByRole("link", { name: "Open" })).getAttribute("href")).toBe("/settings/saved?item=31");
  });

  it("a failure says not saved", async () => {
    api.save.mockResolvedValue({ error: { detail: "no" } });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    render(<SaveReplyButton event={reply as any} />);
    fireEvent.click(screen.getByRole("button", { name: "Save this reply" }));
    expect(await screen.findByText("Not saved. Try again.")).toBeTruthy();
  });
});

describe("Temporary conversation line", () => {
  it("shows the server's retention once it confirms the chat is temporary and yours", async () => {
    api.temp.mockResolvedValue({ data: { retention: "Nothing from it is saved to memory." } });
    render(<TemporaryBanner threadId="tmp-1" />);
    expect(await screen.findByTestId("temporary-banner")).toBeTruthy();
  });

  it("shows nothing for a thread that is not", async () => {
    api.temp.mockResolvedValue({ error: { detail: "Not found" } });
    render(<TemporaryBanner threadId="tmp-2" />);
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByTestId("temporary-banner")).toBeNull();
  });
});
