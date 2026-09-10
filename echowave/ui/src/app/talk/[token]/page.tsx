"use client";

/**
 * The share page: one link where a prospect talks to an agent, no account.
 *
 * The demo you text a client instead of screen-sharing. It is the website
 * widget on a page we host, so the same token (and the same off switch)
 * covers both, and our own origin is always allowed server-side. Nothing
 * here is behind auth, and nothing here leaks more than the widget already
 * does on a customer's site: the agent's name and its theme.
 */

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

type Config = { agent_name?: string | null; button_text?: string; theme?: string };

declare global {
  interface Window {
    DecibylWidget?: { start: () => void; stop: () => void };
  }
}

export default function TalkPage() {
  const token = useParams<{ token: string }>().token;
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch(`/api/v1/public/embed/config/${encodeURIComponent(token)}`);
        if (!response.ok) {
          // A spent daily limit or a switched-off link comes back as a 403
          // with a sentence the visitor can act on; show that one.
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

  // The widget script mounts its own call UI into the page. Loaded once,
  // after the config says the token is good, so a dead link shows a
  // sentence rather than a broken button.
  useEffect(() => {
    if (!config || loaded) return;
    const script = document.createElement("script");
    const api = window.location.origin;
    script.src = `/embed/decibyl-widget.js?token=${encodeURIComponent(token)}&apiEndpoint=${encodeURIComponent(api)}`;
    script.async = true;
    script.onload = () => setLoaded(true);
    document.body.appendChild(script);
    return () => {
      script.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config, token]);

  const name = config?.agent_name?.trim() || "this agent";

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background px-6 py-16 text-foreground">
      <div className="w-full max-w-md text-center">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Voice agent</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight" data-testid="talk-title">
          {error ? "Not available" : `Talk to ${name}`}
        </h1>
        <p className="mt-3 text-sm text-muted-foreground">
          {error
            ? error
            : "Press the button, allow the microphone, and speak as you would on a call. The call is recorded and transcribed for the person who shared this link."}
        </p>
        {!error && !loaded && config && (
          <p className="mt-6 text-xs text-muted-foreground">Loading…</p>
        )}
      </div>
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
    </main>
  );
}
