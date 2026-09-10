import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TalkPage from "../page";

vi.mock("next/navigation", () => ({ useParams: () => ({ token: "emb_test" }) }));

type Call = { url: string; init?: RequestInit };
const calls: Call[] = [];

function json(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) } as Response);
}

beforeEach(() => {
  calls.length = 0;
  Element.prototype.scrollTo = vi.fn();
  global.fetch = vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url.includes("/public/embed/config/")) return json({ agent_name: "Elock support" });
    if (url.endsWith("/public/embed/init")) return json({ session_token: "emb_session_x", workflow_run_id: 1, config: {} });
    if (url.includes("/public/embed/text/emb_session_x/messages")) {
      return json({
        messages: [
          { role: "user", content: "my lock is stuck" },
          { role: "assistant", content: "Sorry to hear that. Is it not opening or not locking?" },
        ],
        is_completed: false,
      });
    }
    return json({ detail: "nope" }, 404);
  }) as unknown as typeof fetch;
});

describe("TalkPage", () => {
  it("names the agent from the config", async () => {
    render(<TalkPage />);
    await waitFor(() => expect(screen.getByTestId("talk-title").textContent).toBe("Talk to Elock support"));
  });

  it("opens a text chat in text mode and renders the transcript the server returns", async () => {
    render(<TalkPage />);
    await screen.findByText("Or chat by typing instead");

    fireEvent.click(screen.getByText("Or chat by typing instead"));

    // init is a text-mode session, so the server prepares a transcript up front
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/public/embed/init"))).toBe(true));
    const init = calls.find((c) => c.url.endsWith("/public/embed/init"))!;
    expect(JSON.parse(String(init.init?.body))).toEqual({ token: "emb_test", mode: "text" });

    const input = await screen.findByPlaceholderText("Type a message…");
    await waitFor(() => expect((input as HTMLInputElement).disabled).toBe(false));
    fireEvent.change(input, { target: { value: "my lock is stuck" } });
    fireEvent.click(screen.getByText("Send"));

    await screen.findByText("Sorry to hear that. Is it not opening or not locking?");
    // the whole transcript comes from the server, user line included
    expect(screen.getByText("my lock is stuck")).toBeTruthy();
  });

  it("shows the link's own sentence when it is not available", async () => {
    (global.fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation((url: string) =>
      url.includes("/config/") ? json({ detail: "This link has used its minutes for today." }, 403) : json({}, 404),
    );
    render(<TalkPage />);
    await waitFor(() => expect(screen.getByTestId("talk-title").textContent).toBe("Not available"));
    expect(screen.getByText("This link has used its minutes for today.")).toBeTruthy();
  });
});
