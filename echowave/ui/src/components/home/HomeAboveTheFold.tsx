"use client";

/**
 * Home is a conversation with Decibyl.
 *
 * The way a Slack workspace opens on Slackbot: a hello, the thread, and a
 * composer. Decibyl is the workspace's own assistant -- it knows the team's
 * numbers, the company's documents, what the business has confirmed, and
 * what every bot did lately (see services/workflow/decibyl.py). The two
 * openers are the questions an owner arrives with, sent as messages so the
 * answer comes from the same brain as everything else typed here.
 *
 * The chips under the hello are built from the account's own state: a
 * failing connector, a paused bot, a missed call to return. A link chip
 * opens the screen that fixes it; a prompt chip opens the shelf.
 */

import { AlertTriangle, ArrowRight, Bell, Sparkles } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  postMessageApiV1TimelineMessagePost,
  teamHomeApiV1TeamHomeGet,
} from "@/client/sdk.gen";
import type { Headline, Suggestion } from "@/client/types.gen";
import { ChannelComposer } from "@/components/channel/ChannelComposer";
import { ChannelStream } from "@/components/channel/ChannelStream";
import { useAuth } from "@/lib/auth";

export const OPENERS = [
  "What happened this week?",
  "What needs my attention today?",
] as const;

/** Built from the reader's own clock. The server's is in a data centre, and
 *  half the accounts would be wished good morning at nine in the evening. */
function partOfDay(now: Date): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

/** What the team did, as a sentence rather than a row of tiles. */
function summarise(headline: Headline): string {
  if (headline.agents === 0) return "Let's put your first agent to work.";
  const parts: string[] = [];
  if (headline.calls > 0) {
    parts.push(
      `${headline.calls} ${headline.calls === 1 ? "call" : "calls"} today`,
    );
    if (headline.answered > 0) parts.push(`${headline.answered} answered`);
    if (headline.outcomes > 0) parts.push(`${headline.outcomes} finished`);
  }
  if (parts.length === 0) {
    return headline.live === 0
      ? "No agent is taking calls right now."
      : "Nothing has come in yet today.";
  }
  const sentence = `${parts.join(", ")}.`;
  if (headline.needs_attention > 0) {
    return `${sentence} ${headline.needs_attention} ${
      headline.needs_attention === 1 ? "agent needs" : "agents need"
    } you.`;
  }
  return sentence;
}

function Chip({ chip }: { chip: Suggestion }) {
  const className =
    "inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";
  if (chip.action === "link" && chip.href) {
    return (
      <Link href={chip.href} className={className}>
        <AlertTriangle className="h-3 w-3" />
        {chip.text}
        <ArrowRight className="h-3 w-3" />
      </Link>
    );
  }
  return (
    <Link href="/marketplace" className={className}>
      {chip.text}
      <ArrowRight className="h-3 w-3" />
    </Link>
  );
}

export function HomeAboveTheFold({ firstName }: { firstName?: string }) {
  const { user, loading: authLoading } = useAuth();
  const [headline, setHeadline] = useState<Headline | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [waitingFor, setWaitingFor] = useState<{
    since: string;
    bots: number[];
  } | null>(null);
  const [sendingOpener, setSendingOpener] = useState<string | null>(null);
  const refreshStream = useRef<() => void>(() => {});
  const registerRefresh = useCallback((refresh: () => void) => {
    refreshStream.current = refresh;
  }, []);

  const greeting = useMemo(() => partOfDay(new Date()), []);

  useEffect(() => {
    if (authLoading || !user) return;
    let cancelled = false;
    (async () => {
      try {
        const response = await teamHomeApiV1TeamHomeGet({
          query: { hours: 24 },
        });
        if (cancelled || response.error || !response.data) return;
        setHeadline(response.data.headline ?? null);
        setSuggestions(response.data.suggestions ?? []);
      } catch {
        // The thread below still works. A greeting that failed to
        // load is a missing sentence, not a broken screen.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  // Decibyl has no workflow id; the thinking row is keyed on 0 and the
  // stream, in assistant mode, clears it on any reply after `since`.
  const asked = () =>
    setWaitingFor({ since: new Date().toISOString(), bots: [0] });

  const sendOpener = async (text: string) => {
    setSendingOpener(text);
    const response = await postMessageApiV1TimelineMessagePost({
      body: { assistant: true, text },
    });
    setSendingOpener(null);
    if (response.error) return;
    asked();
    refreshStream.current();
  };

  return (
    <div className="flex h-[calc(100vh-9rem)] min-h-[32rem] flex-col gap-4">
      {/* The way Slackbot opens: the mark, a hello, one line on what
                happened, and the two questions as cards you press. Centred,
                because this is a greeting and not a form. */}
      <div className="flex flex-col items-center px-2 pt-2 text-center">
        <div
          aria-hidden="true"
          className="flex h-16 w-16 items-center justify-center rounded-2xl bg-rail text-3xl font-semibold text-rail-foreground"
        >
          d
        </div>
        <h2 className="mt-4 text-2xl font-bold tracking-tight">
          Hi, I&apos;m Decibyl!
        </h2>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">
          {greeting}
          {firstName ? `, ${firstName}` : ""}.{" "}
          {headline ? summarise(headline) : ""} I know your bots, your numbers
          and your company&apos;s documents.
        </p>
        <div
          className="mt-4 flex w-full max-w-lg flex-col gap-2"
          aria-label="Ask Decibyl"
        >
          {OPENERS.map((text, index) => {
            const Icon = index === 0 ? Sparkles : Bell;
            return (
              <button
                key={text}
                type="button"
                disabled={sendingOpener !== null}
                onClick={() => void sendOpener(text)}
                className="group flex w-full items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-left text-[15px] font-medium shadow-[var(--shadow-card)] transition-colors hover:bg-muted/40 disabled:opacity-60"
              >
                <Icon
                  aria-hidden="true"
                  className="h-4 w-4 shrink-0 text-muted-foreground"
                />
                <span className="min-w-0 flex-1 truncate">
                  {sendingOpener === text ? "Asking…" : text}
                </span>
                <span
                  aria-hidden="true"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--accent-brand)] text-white transition-transform group-hover:translate-x-0.5"
                >
                  <ArrowRight className="h-4 w-4" />
                </span>
              </button>
            );
          })}
        </div>
        {suggestions.length > 0 ? (
          <div className="mt-3 flex flex-wrap justify-center gap-2">
            {suggestions.map((chip) => (
              <Chip key={`${chip.kind}-${chip.text}`} chip={chip} />
            ))}
          </div>
        ) : null}
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card">
        <ChannelStream
          assistant
          assistantName="Decibyl"
          botNames={{}}
          onRegisterRefresh={registerRefresh}
          waitingFor={waitingFor}
        />
        <ChannelComposer
          assistant
          bots={[]}
          channelName="Decibyl"
          onSent={() => {
            asked();
            refreshStream.current();
          }}
        />
      </div>
    </div>
  );
}
