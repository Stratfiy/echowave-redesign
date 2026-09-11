"use client";

/**
 * The share page: one link where a prospect talks to an agent, no account.
 *
 * The demo you text a client instead of screen-sharing. It offers both ways
 * in: a voice call (the hosted widget), and a text chat that works on any
 * network — an office, a train, a locked-down corporate proxy where WebRTC
 * never connects. Same token, same agent, same off switch; nothing here is
 * behind auth and nothing leaks more than the widget already does.
 */

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

type Config = { agent_name?: string | null; button_text?: string; theme?: string };
type ChatMessage = { role: "user" | "assistant"; content: string };
type VoiceStatus = "idle" | "connecting" | "connected" | "failed";

const VOICE_STATUS_RE = /\bdecibyl-state-(idle|connecting|connected|failed)\b/;

/** The widget's connection state, as it writes it onto its own button. */
function readVoiceStatus(): VoiceStatus | null {
  const cta = document.getElementById("decibyl-widget-cta");
  const match = cta?.className.match(VOICE_STATUS_RE);
  return match ? (match[1] as VoiceStatus) : null;
}

type Caption = { role: "user" | "bot"; text: string; final: boolean };

declare global {
  interface Window {
    DecibylWidget?: {
      start: () => void;
      stop: () => void;
      onTranscript?: (callback: (captions: Caption[]) => void) => void;
    };
  }
}

export default function TalkPage() {
  const token = useParams<{ token: string }>().token;
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [widgetLoaded, setWidgetLoaded] = useState(false);
  // The call's real state, read off the widget rather than off our click.
  // The widget marks its own button with a `decibyl-state-<status>` class as
  // the WebRTC connection moves through connecting / connected / failed; the
  // caption used to flip to "Listening" the moment the orb was tapped, which
  // told a visitor to speak into a call that had not connected.
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus>("idle");
  // Live captions, straight off the widget. The server has always sent these
  // down the signalling socket; nothing on this page was listening.
  const [captions, setCaptions] = useState<Caption[]>([]);

  // Text chat
  const [chatOpen, setChatOpen] = useState(false);
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch(`/api/v1/public/embed/config/${encodeURIComponent(token)}`);
        if (!response.ok) {
          let detail: string | null = null;
          try {
            detail = ((await response.json()) as { detail?: unknown }).detail as string | null;
          } catch {
            detail = null;
          }
          setError(
            response.status === 404
              ? "This link is not valid."
              : typeof detail === "string" && detail.includes("minutes for today")
                ? detail
                : "This agent is not available on this link right now.",
          );
          return;
        }
        setConfig((await response.json()) as Config);
      } catch {
        setError("Could not reach the agent. Try again in a moment.");
      }
    })();
  }, [token]);

  // The voice widget mounts its own floating call UI. Loaded once, after the
  // config says the token is good, so a dead link shows a sentence rather than
  // a broken button.
  useEffect(() => {
    if (!config || widgetLoaded) return;
    const script = document.createElement("script");
    const api = window.location.origin;
    script.src = `/embed/decibyl-widget.js?token=${encodeURIComponent(token)}&apiEndpoint=${encodeURIComponent(api)}`;
    script.async = true;
    script.onload = () => setWidgetLoaded(true);
    document.body.appendChild(script);
    return () => {
      script.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config, token]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  // Watch the whole body rather than the button: the widget creates its
  // button asynchronously after its own config fetch, so the element does
  // not exist yet when this page mounts, and it re-renders the class on every
  // status change thereafter. One observer covers both.
  useEffect(() => {
    if (!widgetLoaded) return;
    const sync = () => {
      const status = readVoiceStatus();
      if (status) setVoiceStatus(status);
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, [widgetLoaded]);

  // Captions arrive as the whole list rather than a delta, because an interim
  // line is edited in place rather than appended — see the widget's own note.
  useEffect(() => {
    if (!widgetLoaded) return;
    window.DecibylWidget?.onTranscript?.((next) => setCaptions(next));
  }, [widgetLoaded]);

  const name = config?.agent_name?.trim() || "this agent";

  function startVoice() {
    window.DecibylWidget?.start();
  }

  async function openChat() {
    setChatOpen(true);
    if (sessionToken) return;
    setChatError(null);
    try {
      const res = await fetch(`/api/v1/public/embed/init`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, mode: "text" }),
      });
      if (!res.ok) {
        setChatError("Could not start the chat. The link may be paused or out of minutes.");
        return;
      }
      const data = (await res.json()) as { session_token: string };
      setSessionToken(data.session_token);
    } catch {
      setChatError("Could not start the chat. Try again in a moment.");
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || !sessionToken || sending) return;
    setInput("");
    setChatError(null);
    setMessages((m) => [...m, { role: "user", content: text }]);
    setSending(true);
    try {
      const res = await fetch(
        `/api/v1/public/embed/text/${encodeURIComponent(sessionToken)}/messages`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        },
      );
      if (!res.ok) {
        setChatError("That message did not go through. Try again.");
        return;
      }
      const data = (await res.json()) as { messages: ChatMessage[] };
      // The server returns the whole transcript, so it is the source of truth.
      setMessages(data.messages.filter((mm) => mm.role === "user" || mm.role === "assistant"));
    } catch {
      setChatError("That message did not go through. Try again.");
    } finally {
      setSending(false);
    }
  }

  if (error) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center bg-background px-6 py-16 text-foreground">
        <div className="w-full max-w-md text-center">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Voice agent</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight" data-testid="talk-title">
            Not available
          </h1>
          <p className="mt-3 text-sm text-muted-foreground">{error}</p>
        </div>
        <Footer />
      </main>
    );
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background px-6 py-16 text-foreground">
      <div className="w-full max-w-md text-center">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Voice agent</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight" data-testid="talk-title">
          Talk to {name}
        </h1>
        <p className="mt-3 text-sm text-muted-foreground">
          Press the orb and speak as you would on a call, or use the chat if you would rather type
          or your network blocks calls. The call is recorded and transcribed for the person who
          shared this link.
        </p>

        {/* The orb: the affordance and the live state in one. */}
        <div className="mt-10 flex flex-col items-center">
          <button
            type="button"
            onClick={startVoice}
            disabled={!widgetLoaded}
            aria-label={voiceStatus === "connected" ? "In call" : "Start voice call"}
            className="group relative flex h-36 w-36 items-center justify-center rounded-full outline-none disabled:cursor-not-allowed"
          >
            {(!widgetLoaded || voiceStatus === "connecting" || voiceStatus === "connected") && (
              <>
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/15" />
                <span className="absolute inline-flex h-28 w-28 animate-ping rounded-full bg-primary/20 [animation-delay:300ms]" />
              </>
            )}
            <span className="relative inline-flex h-24 w-24 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform group-hover:scale-105 group-active:scale-95">
              <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
              </svg>
            </span>
          </button>
          <p className="mt-4 text-xs text-muted-foreground" data-testid="voice-caption">
            {!widgetLoaded
              ? "Preparing…"
              : voiceStatus === "connecting"
                ? "Connecting…"
                : voiceStatus === "connected"
                  ? "Listening — speak now"
                  : voiceStatus === "failed"
                    ? "Couldn't connect — try the chat below"
                    : "Tap to talk"}
          </p>

          {/* Live captions. Only while there is something to show: an empty
              panel under the orb on an idle page is furniture, and it pushes
              the "Type instead" link below the fold on a phone. */}
          {captions.length > 0 && (
            <div
              className="mt-5 w-full max-w-md space-y-2 text-left"
              data-testid="voice-captions"
              aria-live="polite"
            >
              {captions.map((caption, index) => (
                <p
                  key={`${index}-${caption.role}`}
                  className={
                    caption.role === "user"
                      ? `text-sm text-foreground${caption.final ? "" : " opacity-60"}`
                      : `text-sm font-medium text-primary${caption.final ? "" : " opacity-80"}`
                  }
                >
                  <span className="mr-1.5 text-[0.7rem] uppercase tracking-wide text-muted-foreground">
                    {caption.role === "user" ? "You" : name}
                  </span>
                  {caption.text}
                </p>
              ))}
            </div>
          )}

          {!chatOpen && (
            <button
              type="button"
              onClick={openChat}
              className="mt-6 text-sm font-medium text-foreground underline-offset-4 hover:underline"
            >
              Or chat by typing instead
            </button>
          )}
        </div>

        {/* Text chat: works where a call cannot. */}
        {chatOpen && (
          <div className="mt-8 overflow-hidden rounded-2xl border border-border bg-card text-left">
            <div ref={scrollRef} className="max-h-72 space-y-3 overflow-y-auto px-4 py-4">
              {messages.length === 0 && !chatError && (
                <p className="text-center text-xs text-muted-foreground">
                  {sessionToken ? `Say hi to ${name}.` : "Starting the chat…"}
                </p>
              )}
              {messages.map((m, i) => (
                <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
                  <span
                    className={
                      "inline-block max-w-[85%] rounded-2xl px-3 py-2 text-sm " +
                      (m.role === "user"
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted text-foreground")
                    }
                  >
                    {m.content}
                  </span>
                </div>
              ))}
              {sending && (
                <div className="flex justify-start">
                  <span className="inline-flex items-center gap-1 rounded-2xl bg-muted px-3 py-2">
                    <Dot /> <Dot delay="150ms" /> <Dot delay="300ms" />
                  </span>
                </div>
              )}
            </div>
            {chatError && <p className="px-4 pb-2 text-xs text-destructive">{chatError}</p>}
            <form
              className="flex items-center gap-2 border-t border-border px-3 py-2"
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={!sessionToken}
                placeholder="Type a message…"
                className="flex-1 bg-transparent px-1 py-1.5 text-sm outline-none placeholder:text-muted-foreground"
              />
              <button
                type="submit"
                disabled={!sessionToken || sending || !input.trim()}
                className="rounded-full bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-40"
              >
                Send
              </button>
            </form>
          </div>
        )}
      </div>
      <Footer />
    </main>
  );
}

function Dot({ delay = "0ms" }: { delay?: string }) {
  return (
    <span
      className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground"
      style={{ animationDelay: delay }}
    />
  );
}

function Footer() {
  return (
    <p className="mt-12 text-xs text-muted-foreground">
      Built with{" "}
      <a href="https://decibyl.ai" className="font-medium text-foreground underline-offset-4 hover:underline">
        Decibyl
      </a>
      . Voice agents for Indian businesses.{" "}
      <a href="/auth/signup" className="font-medium text-foreground underline-offset-4 hover:underline">
        Make your own
      </a>
      .
    </p>
  );
}
