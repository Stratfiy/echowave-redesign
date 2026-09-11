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

  it("drives the orb caption from the widget's real connection state, not the tap", async () => {
    render(<TalkPage />);
    await screen.findByText("Or chat by typing instead");

    // The widget script is appended by the page in an effect, which flushes
    // a beat after the text above renders — so wait for the element itself.
    // Then fire its onload so the observer arms, and stand in for the
    // button the widget would create.
    await waitFor(() =>
      expect(document.querySelector('script[src*="decibyl-widget.js"]')).not.toBeNull(),
    );
    const script = document.querySelector('script[src*="decibyl-widget.js"]') as HTMLScriptElement;
    script.onload?.(new Event("load"));

    const cta = document.createElement("button");
    cta.id = "decibyl-widget-cta";
    cta.className = "decibyl-widget-cta decibyl-state-idle";
    document.body.appendChild(cta);
    await waitFor(() => expect(screen.getByTestId("voice-caption").textContent).toBe("Tap to talk"));

    // Tapping the orb must not claim we are listening — nothing has connected.
    fireEvent.click(screen.getByLabelText("Start voice call"));
    expect(screen.getByTestId("voice-caption").textContent).toBe("Tap to talk");

    cta.className = "decibyl-widget-cta decibyl-state-connecting";
    await waitFor(() => expect(screen.getByTestId("voice-caption").textContent).toBe("Connecting…"));

    cta.className = "decibyl-widget-cta decibyl-state-connected";
    await waitFor(() =>
      expect(screen.getByTestId("voice-caption").textContent).toBe("Listening — speak now"),
    );

    cta.className = "decibyl-widget-cta decibyl-state-failed";
    await waitFor(() =>
      expect(screen.getByTestId("voice-caption").textContent).toBe("Couldn't connect — try the chat below"),
    );
  });

  it("renders live captions from the widget, and nothing before there are any", async () => {
    let emit: ((captions: { role: string; text: string; final: boolean }[]) => void) | null = null;
    (window as unknown as { DecibylWidget: unknown }).DecibylWidget = {
      start: vi.fn(),
      stop: vi.fn(),
      onTranscript: (cb: (c: { role: string; text: string; final: boolean }[]) => void) => {
        emit = cb;
      },
    };

    render(<TalkPage />);
    await waitFor(() =>
      expect(document.querySelector('script[src*="decibyl-widget.js"]')).not.toBeNull(),
    );
    const script = document.querySelector('script[src*="decibyl-widget.js"]') as HTMLScriptElement;
    script.onload?.(new Event("load"));

    // An empty panel under the orb is furniture, and on a phone it pushes the
    // "type instead" link below the fold.
    await waitFor(() => expect(emit).not.toBeNull());
    expect(screen.queryByTestId("voice-captions")).toBeNull();

    emit!([
      { role: "user", text: "my lock is stuck", final: true },
      { role: "bot", text: "Is it not opening, or not locking?", final: false },
    ]);

    await waitFor(() => expect(screen.getByTestId("voice-captions")).toBeTruthy());
    const panel = screen.getByTestId("voice-captions");
    expect(panel.textContent).toContain("my lock is stuck");
    expect(panel.textContent).toContain("Is it not opening, or not locking?");
    // The agent's own name, not "Bot" — the visitor is talking to a clinic or
    // a support desk, not to a chatbot.
    expect(panel.textContent).toContain("Elock support");
    expect(panel.textContent).toContain("You");
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
